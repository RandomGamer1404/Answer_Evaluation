import os
import re
import nltk
import torch
import pandas as pd
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from config import settings
import logging
import math
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from huggingface_hub import InferenceClient
import requests

# ADD MISSING IMPORTS
import json
import time
from typing import Dict, List, Any
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer
from difflib import SequenceMatcher   # <-- ADD for fuzzy matching

logger = logging.getLogger(__name__)

# Guard SBERT import so it won't crash if incompatible with huggingface_hub
try:
    from sentence_transformers import SentenceTransformer, util
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except Exception as e:
    logger.warning(f"Sentence-Transformers unavailable, semantic pruning disabled: {e}")
    SentenceTransformer = None
    util = None
    SENTENCE_TRANSFORMERS_AVAILABLE = False

class PDFAnswerEvaluator:
    def __init__(self,
                 embedding_model: str = None,
                 llm_model_name: str = None,
                 load_in_4bit: bool = None,
                 fast_mode: bool = None):
        
        # Use settings defaults if not provided
        self.embedding_model = embedding_model or settings.EMBEDDING_MODEL
        self.llm_model_name = llm_model_name or settings.LLM_MODEL_NAME
        self.load_in_4bit = load_in_4bit if load_in_4bit is not None else settings.LOAD_IN_4BIT
        self.fast_mode = settings.FAST_MODE if fast_mode is None else fast_mode

        # NLTK Setup
        nltk.download('stopwords', quiet=True)
        nltk.download('wordnet', quiet=True)
        nltk.download('punkt', quiet=True)
        self.stop_words = set(stopwords.words('english'))
        self.lemmatizer = WordNetLemmatizer()

        # Semantic Embedding Model (optional)
        self.sbert_model = SentenceTransformer(self.embedding_model) if SENTENCE_TRANSFORMERS_AVAILABLE else None

        # Remote vs local LLM
        self.use_hf = (getattr(settings, "LLM_BACKEND", "local") == "hf")
        self.hf_client = None

        # Local LLM: only if NOT using HF
        self.tokenizer = None
        self.llm_model = None

        if self.use_hf:
            # Use HF InferenceClient with chat.completions (provider auto)
            self.hf_client = InferenceClient(api_key=settings.HF_TOKEN)
            logger.info("Using HF InferenceClient (chat.completions)")
        elif not self.fast_mode:
            self._setup_llm()
        
        # Storage for results
        self.evaluation_results = []
        
        # Caching
        self._pdf_content_cache: Dict[str, str] = {}
        self._extracted_question_cache: Dict[str, Dict[int, Dict]] = {}
        self._load_cache()

    def _setup_llm(self):
        """Setup the LLM model"""
        if self.llm_model is not None and self.tokenizer is not None:
            return
        logger.info(f"Loading LLM: {self.llm_model_name} locally...")
        self.tokenizer = AutoTokenizer.from_pretrained(self.llm_model_name)
        
        # Set pad token if not exists
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        if torch.cuda.is_available() and self.load_in_4bit:
            logger.info("CUDA is available and load_in_4bit is True. Loading model in 4-bit quantization.")
            nf4_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.bfloat16
            )
            self.llm_model = AutoModelForCausalLM.from_pretrained(
                self.llm_model_name,
                quantization_config=nf4_config,
                device_map="auto"
            )
        elif torch.cuda.is_available():
            logger.info("CUDA is available. Loading model in full precision.")
            self.llm_model = AutoModelForCausalLM.from_pretrained(
                self.llm_model_name,
                torch_dtype=torch.float16,
                device_map="auto"
            )
        else:
            logger.info("CUDA not available. Loading model on CPU.")
            self.llm_model = AutoModelForCausalLM.from_pretrained(
                self.llm_model_name,
                device_map="cpu"
            )

        self.llm_model.eval()

    def _ensure_llm(self):
        """Ensure LLM is ready (local) or HF client exists (remote)."""
        if self.use_hf:
            if self.hf_client is None:
                self.hf_client = InferenceClient(
                    model=settings.HF_INFERENCE_MODEL,
                    token=settings.HF_TOKEN,
                )
            return
        if self.llm_model is None or self.tokenizer is None:
            self._setup_llm()

    # Validate HF token
    def _check_hf_token_valid(self) -> bool:
        if not settings.HF_TOKEN:
            logger.error("HF_TOKEN is empty.")
            return False
        try:
            r = requests.get("https://huggingface.co/api/whoami-v2",
                             headers={"Authorization": f"Bearer {settings.HF_TOKEN}"}, timeout=10)
            if r.status_code == 200:
                logger.info(f"HF token valid for user: {r.json().get('name', 'unknown')}")
                return True
            logger.error(f"HF token invalid: {r.status_code} {r.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"HF token validation failed: {e}")
            return False

    # Sanitize almost-JSON to valid JSON
    def _sanitize_json_response(self, raw: str) -> str:
        if not raw:
            return ""
        s = raw.strip()
        if s.startswith('```'):
            s = re.sub(r'^```[a-zA-Z0-9]*\n?', '', s)
            s = re.sub(r'\n?```$', '', s)
        # Keep only outer-most JSON object if extra prose exists
        start = s.find('{'); end = s.rfind('}')
        if start != -1 and end != -1 and end > start:
            s = s[start:end+1]
        # Normalize quotes
        s = s.replace('“','"').replace('”','"').replace("’","'").replace("`", '"')
        # Quote unquoted keys
        s = re.sub(r'(?P<prefix>[\{\s,])(?P<key>[A-Za-z_][A-Za-z0-9_\- ]*)\s*:', r'\g<prefix>"\g<key>":', s)
        # FIX: add missing commas between adjacent objects inside arrays, e.g. ...}{...
        s = re.sub(r'\}\s*\n\s*\{', '},{', s)
        s = re.sub(r'\}\s*\{', '},{', s)
        # Remove trailing commas before } or ]
        s = re.sub(r',\s*([}\]])', r'\1', s)
        # Balance braces/brackets
        ob, cb = s.count('{'), s.count('}')
        if cb < ob: s += '}' * (ob - cb)
        ob, cb = s.count('['), s.count(']')
        if cb < ob: s += ']' * (ob - cb)
        return s

    # Heuristic extraction when JSON is malformed: pull out point_evaluations with regex
    def _extract_point_evals_from_text(self, raw: str) -> List[Dict[str, Any]]:
        if not raw:
            return []
        txt = raw
        # Try to capture triplets: expert_point_text, score, feedback_on_point in any spacing/newlines
        triplet_rx = re.compile(
            r'"expert_point_text"\s*:\s*"(?P<text>[^"]+)"\s*,\s*'
            r'"score"\s*:\s*(?P<score>-?\d+(?:\.\d+)?)\s*,\s*'
            r'"feedback_on_point"\s*:\s*"(?P<fb>[^"]*)"',
            re.IGNORECASE | re.DOTALL
        )
        evals = []
        for m in triplet_rx.finditer(txt):
            t = m.group('text').strip()
            try:
                sc = float(m.group('score'))
                sc = max(0.0, min(1.0, sc))
            except Exception:
                sc = 0.0
            fb = m.group('fb').strip()
            evals.append({
                "expert_point_text": t,
                "score": sc,
                "feedback_on_point": fb
            })
        return evals

    # TSV fallback: one line per expert point (index-based), parse reliably
    def _parse_tsv_scores(self, tsv: str, expert_points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        lines = [l.strip() for l in tsv.splitlines() if l.strip()]
        rows = []
        for l in lines:
            # Expected: index<TAB>score<TAB>feedback
            parts = l.split('\t')
            if len(parts) < 2:
                continue
            try:
                idx = int(parts[0])
                sc = float(parts[1])
                sc = max(0.0, min(1.0, sc))
            except Exception:
                continue
            fb = parts[2] if len(parts) > 2 else ""
            if 1 <= idx <= len(expert_points):
                rows.append({
                    "expert_point_text": expert_points[idx - 1]["point_text"],
                    "score": sc,
                    "feedback_on_point": fb
                })
        return rows

    def generate_llm_response(self, prompt_text: str, max_tokens: int = None, temperature: float = 0.0,
                              timeout_seconds: int = None, input_truncate_tokens: int = None) -> str:
        """Use HF chat.completions; fallback to text_generation if needed."""
        self._ensure_llm()
        if not self._check_hf_token_valid():
            return ""
        max_tokens = max_tokens or settings.MAX_NEW_TOKENS_EVAL
        timeout_seconds = timeout_seconds or settings.GEN_TIMEOUT_EVAL
        prompt = prompt_text[-8000:] if input_truncate_tokens else prompt_text

        if self.use_hf:
            models_to_try = [getattr(settings, "HF_INFERENCE_MODEL", None)] + list(getattr(settings, "HF_FALLBACK_MODELS", []))
            models_to_try = [m for m in models_to_try if m]

            # Prefer chat for conversational models (Mistral Instruct)
            for model_id in models_to_try:
                logger.info(f"HF chat.completions start (model={model_id}, max_tokens={max_tokens})")
                text = self._hf_chat_completion_via_client(model_id, prompt, max_tokens, temperature, timeout_seconds)
                logger.info(f"HF chat.completions returned {len(text)} chars for {model_id}")
                if text:
                    return text

            # Fallback to text_generation for non-chat models
            for model_id in models_to_try:
                logger.info(f"HF text_generation start (model={model_id}, max_new_tokens={max_tokens})")
                text = self._hf_text_generation(model_id, prompt, max_tokens, temperature)
                logger.info(f"HF text_generation returned {len(text)} chars for {model_id}")
                if text:
                    return text

            logger.warning("HF Inference returned empty for all tried models.")
            return ""

        logger.warning("LLM API unavailable (use_hf=False).")
        return ""

    # ---------------------- CLEAN TEXT (preserve newlines) ----------------------
    def _clean_text(self, text: str) -> str:
        """Clean and normalize extracted text but preserve line breaks for parsing."""
        if not text:
            return ""
        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        # Remove hyphenated line breaks like "micro-\nscope" -> "microscope"
        text = re.sub(r'-\s*\n', '', text)
        # Collapse spaces and tabs but keep newlines
        text = re.sub(r'[ \t\f\v]+', ' ', text)
        # Trim trailing spaces before newline
        text = re.sub(r'[ \t]+\n', '\n', text)
        # Squash many blank lines to a maximum of one blank line
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Remove non-ASCII artifacts that often come from OCR
        text = re.sub(r'[^\x00-\x7F]+', '', text)
        return text.strip()

    # ---------------------- CACHING ----------------------
    def _load_cache(self):
        """Load cached data from disk"""
        try:
            if os.path.exists(settings.CACHE_FILE):
                import json
                with open(settings.CACHE_FILE, 'r') as f:
                    cache_data = json.load(f)
                    self._pdf_content_cache = cache_data.get('pdf_content', {})
                    self._extracted_question_cache = cache_data.get('extracted_questions', {})
                logger.info(f"Loaded cache with {len(self._pdf_content_cache)} PDF contents and {len(self._extracted_question_cache)} question extractions")
        except Exception as e:
            logger.warning(f"Could not load cache: {e}")
            self._pdf_content_cache = {}
            self._extracted_question_cache = {}

    def _save_cache(self):
        """Save cache to disk"""
        try:
            import json
            cache_data = {
                'pdf_content': self._pdf_content_cache,
                'extracted_questions': self._extracted_question_cache
            }
            with open(settings.CACHE_FILE, 'w') as f:
                json.dump(cache_data, f, indent=4)
            logger.info("Saved cache to evaluation_cache.json")
        except Exception as e:
            logger.warning(f"Could not save cache: {e}")

    # ---------------------- TEXT PROCESSING ----------------------
    def _find_section(self, text: str, qnum: int) -> str:
        """Find the section for a specific question number with robust patterns and char-level fallback."""
        if not text:
            return ""
        cleaned = self._clean_text(text)
        lines = cleaned.split('\n')

        # Primary line-anchored patterns (handle '# Q1', 'Q 1', 'Question 1', 'Ans 1', 'A1', '(Q1)' and OCR confusions)
        def make_line_patterns(n: int):
            return [
                rf'^(?:#+\s*)?Q(?:uestion)?\s*{n}\s*[).: -]+',     # Q1. / Question 1:
                rf'^(?:#+\s*)?Q\s*{n}\s*[).: -]+',                 # Q 1:
                rf'^(?:#+\s*)?(?:Ans(?:wer)?|A)\s*{n}\s*[).: -]+', # Ans 1:
                rf'^\(?\s*Q(?:uestion)?\s*{n}\s*\)?\s*[).: -]+',   # (Q1):
                rf'^(?:#+\s*)?[QO0]\s*{n}\s*[).: -]+'              # OCR: O1. / 01.
            ]

        def numeric_header(n: int):
            return rf'^\s*\(?\s*{n}\s*\)?\s*[).: -]+\s+'

        def find_line_index(n: int) -> int | None:
            pats = [re.compile(p, re.IGNORECASE) for p in make_line_patterns(n)]
            for i, ln in enumerate(lines):
                for pat in pats:
                    if pat.search(ln):
                        return i
            num_pat = re.compile(numeric_header(n), re.IGNORECASE)
            for i, ln in enumerate(lines):
                if num_pat.match(ln):
                    prev_blank = (i == 0) or (not lines[i-1].strip())
                    if prev_blank:
                        return i
            return None

        start_idx = find_line_index(qnum)

        # Char-level fallback when headings are inline (e.g., '... Q2 ...' on same line)
        if start_idx is None:
            m = re.search(rf'(?i)(^|[^A-Za-z])Q(?:uestion)?\s*{qnum}\s*[).: -]?', cleaned)
            if m:
                start_char = m.start() if m.start() >= 0 else 0
                # Find next question by char
                m2 = re.search(rf'(?i)(^|[^A-Za-z])Q(?:uestion)?\s*{qnum+1}\s*[).: -]?', cleaned[start_char+1:])
                end_char = (start_char + 1 + m2.start()) if m2 else len(cleaned)
                return cleaned[start_char:end_char].strip()
            # Last fallback: numeric-only in text
            m = re.search(rf'(?i)(^|\s|\()({qnum})\s*[).: -]+', cleaned)
            if m:
                start_char = m.start()
                m2 = re.search(rf'(?i)(^|\s|\()({qnum+1})\s*[).: -]+', cleaned[start_char+1:])
                end_char = (start_char + 1 + m2.start()) if m2 else len(cleaned)
                return cleaned[start_char:end_char].strip()
            return ""

        # Find end_idx by next question on lines or via char-level fallback
        next_idx = find_line_index(qnum + 1)
        if next_idx is not None and next_idx > start_idx:
            end_idx = next_idx
            return '\n'.join(lines[start_idx:end_idx]).strip()

        # If next not found line-wise, do char-level for next
        # Compute char span for start line
        prefix_len = sum(len(l) + 1 for l in lines[:start_idx])  # +1 for '\n'
        start_char = prefix_len
        m2 = re.search(rf'(?i)(^|[^A-Za-z])Q(?:uestion)?\s*{qnum+1}\s*[).: -]?', cleaned[start_char+1:])
        end_char = (start_char + 1 + m2.start()) if m2 else len(cleaned)
        return cleaned[start_char:end_char].strip()

    def _extract_answer_key_fast(self, full_text: str, qnum: int) -> Dict:
        """Structured extraction for answer key:
        - Detect total marks
        - Extract numbered/bulleted points
        """
        section = self._find_section(full_text, qnum)
        if not section:
            return {}

        # Clean noise
        section = self._clean_text(section)

        # Drop leading 'Answer:' labels if present
        section = re.sub(r'(?im)^\s*answer\s*:?\s*', '', section)

        lines = [l.strip() for l in section.split('\n') if l.strip()]
        if not lines:
            return {}

        # Question line (first non-empty)
        question_line = lines[0]
        question_text = re.sub(rf'^\s*Q(?:uestion)?\s*{qnum}\s*[).:]*\s*', '', question_line, flags=re.IGNORECASE).strip()
        # Merge a continuation header line like "of Thermodynamics (6 Marks) 1."
        if len(question_text) < 8 and len(lines) > 1 and not re.match(r'^\s*(?:[-*•]|\d+[\).\-\:])\s+', lines[1]):
            cont = re.sub(r'\[?\(?\s*\d+\s*marks?\s*\)?\]?', '', lines[1], flags=re.IGNORECASE)
            cont = re.sub(r'\s+\d+\.\s*$', '', cont).strip(' .-')
            if cont:
                question_text = f"{question_text} {cont}".strip()

        # Total marks
        marks_patterns = [
            r'\[\s*(\d+)\s*Marks?\s*\]',
            r'\(\s*(\d+)\s*Marks?\s*\)',
            r'\[\s*(\d+)\s*Mark\s*\]',
            r'\(\s*(\d+)\s*Mark\s*\)',
            r'(?i)\btotal\s*marks\s*[:\-]?\s*(\d+)\b'
        ]
        total_marks = None
        for mp in marks_patterns:
            m = re.search(mp, section, flags=re.IGNORECASE)
            if m:
                try:
                    total_marks = int(m.group(1))
                    break
                except:
                    pass
        if total_marks is None:
            total_marks = 5  # fallback

        # Regex for numbered points like: "1. Title (2 marks):"
        point_header_rx = re.compile(
            r'^\s*(\d+)\.\s*(.+?)\s*(?:\(\s*(\d+)\s*mark[s]?\s*\))?\s*:?\s*$',
            re.IGNORECASE
        )

        # Additional bullets: "- text", "* text", "• text"
        bullet_md_rx = re.compile(r'^\s*[-*•]\s+(?P<txt>.+)$')
        # Paren/roman numerals: "i) text", "(i) text", "1) text"
        roman_rx = re.compile(r'^\s*\(?\s*(?P<num>(?:[ivx]+|\d+))\s*\)?[.)\-:]\s+(?P<txt>.+)$', re.IGNORECASE)

        # Inline marks anywhere in a line
        inline_marks_rx = re.compile(r'\(\s*(\d+)\s*mark[s]?\s*\)', re.IGNORECASE)

        expert_points: List[Dict[str, Any]] = []

        # Pass 1: structured "1. Title (marks):" with optional description accumulation
        current_point = None
        for i, line in enumerate(lines[1:]):
            header_match = point_header_rx.match(line)
            if header_match:
                # Flush previous
                if current_point:
                    desc = ' '.join(current_point.get('desc_parts', [])).strip()
                    pt = current_point['title']
                    if desc:
                        pt = f"{pt} {desc}"
                    expert_points.append({"point_text": pt, "point_marks": current_point['marks']})
                idx, title, marks = header_match.groups()
                marks_val = int(marks) if marks else None
                current_point = {"index": int(idx), "title": title.strip(), "marks": marks_val or 1, "desc_parts": []}
                continue

            # Stop when a new question header is detected
            if re.match(r'^\s*Q(?:uestion)?\s*\d+', line, flags=re.IGNORECASE):
                break

            if current_point:
                current_point['desc_parts'].append(line)

        if current_point:
            desc = ' '.join(current_point.get('desc_parts', [])).strip()
            pt = current_point['title']
            if desc:
                pt = f"{pt} {desc}"
            expert_points.append({"point_text": pt, "point_marks": current_point['marks']})

        # Pass 2: if empty, collect Markdown bullets and roman/paren numbered items
        if not expert_points:
            bullets = []
            for line in lines[1:]:
                m = bullet_md_rx.match(line) or roman_rx.match(line)
                if m:
                    # Support both regex groups
                    txt = m.groupdict().get('txt') or m.group(0)
                    if txt:
                        # Remove inline "(N mark[s])" and trailing punctuation
                        in_m = inline_marks_rx.search(txt)
                        marks_val = int(in_m.group(1)) if in_m else None
                        txt_clean = inline_marks_rx.sub('', txt).strip(':- ').strip()
                        bullets.append({"point_text": txt_clean, "point_marks": marks_val or 1})

            # If we found bullets, use them
            if bullets:
                expert_points = bullets

        # Pass 3: last resort — split into short sentences as points
        if not expert_points:
            body = "\n".join(lines[1:]).strip()
            # Prefer lines that look like list items
            candidates = [l for l in lines[1:] if re.match(r'^\s*(?:[-*•]|\d+[\).\-\:])\s+', l)]
            if not candidates:
                # Fallback to sentences
                candidates = [s.strip() for s in re.split(r'(?<=[.!?])\s+', body) if len(s.strip()) > 8]
            # Take up to 6 points
            candidates = candidates[:6]
            if candidates:
                # Clean and build points
                tmp_points = []
                for c in candidates:
                    c = re.sub(r'^\s*(?:[-*•]|\(?[ivxIVX\d]+\)?[.)\-\:])\s+', '', c).strip()
                    c = inline_marks_rx.sub('', c).strip()
                    if len(c) >= 8:
                        tmp_points.append({"point_text": c, "point_marks": 1})
                expert_points = tmp_points

        # If marks weren’t specified, distribute evenly to match total_marks
        if expert_points:
            n = len(expert_points)
            # If all marks are default 1 but total_marks >> n, distribute
            if sum(p.get("point_marks", 1) for p in expert_points) < total_marks:
                base = max(1, total_marks // n)
                rem = max(0, total_marks - base * n)
                for i, p in enumerate(expert_points):
                    p["point_marks"] = base + (1 if i < rem else 0)
            else:
                # Ensure each has point_marks
                for p in expert_points:
                    p["point_marks"] = int(p.get("point_marks") or 1)

        # Final clean: remove ultra-short fragments
        expert_points = [p for p in expert_points if len(p["point_text"]) > 8]

        if not expert_points:
            logger.warning(f"No expert points parsed for Q{qnum}. Section (first 200 chars): {section[:200]}")

        return {
            "question_text": question_text or f"Question {qnum}",
            "total_marks": total_marks,
            "expert_points": expert_points
        }

    def _extract_student_answer_fast(self, full_text: str, qnum: int) -> Dict:
        """Extract student answer block with robust fallbacks."""
        section = self._find_section(full_text, qnum)

        # Fallback: split into sequential blocks and pick the nth if _find_section failed
        if not section:
            blocks = self._split_student_blocks(full_document_text:=full_text)
            if blocks:
                idx = min(max(0, qnum - 1), len(blocks) - 1)
                section = blocks[idx]

        if not section:
            return {}

        section = self._clean_text(section)
        # Remove leading "Qn" heading variants (Q / O / 0)
        section = re.sub(rf'^\s*(?:Q|O|0)?\s*{qnum}\s*[).: -]*\s*', '', section, flags=re.IGNORECASE)
        # Remove bullet symbols
        section = re.sub(r'[•●▪]', '', section)
        # Fix merged enumerations like "1. 2. 3. 4." -> keep first if no text followed
        section = re.sub(r'(\d+\.)\s*(\d+\.\s*){2,}', r'\1 ', section)
        # Drop standalone large enumerators like "42."
        section = re.sub(r'(?m)^\s*\d{2,}\.\s*$', '', section)
        # Strip excessive spaces
        section = re.sub(r'\n{3,}', '\n\n', section).strip()
        return {"student_answer_text": section}

    # NEW: unify cached extraction for answer key and student answers
    def _get_extracted_data_from_cache_or_llm(self, question_num: int, full_document_text: str,
                                              cache_key: str, doc_type: str) -> Dict:
        """
        Return parsed data for a question, using in-memory/on-disk cache when available.
        doc_type: 'answer_key' | 'student_answers'
        """
        # Normalize inputs
        doc_type = (doc_type or "").strip().lower()
        if doc_type not in ("answer_key", "student_answers"):
            logger.warning(f"Unknown doc_type '{doc_type}', defaulting to 'student_answers'")
            doc_type = "student_answers"

        # Ensure cache containers
        if cache_key not in self._extracted_question_cache:
            self._extracted_question_cache[cache_key] = {}

        q_cache = self._extracted_question_cache[cache_key]

        # Respect IGNORE_CACHE_FOR_EXTRACTION
        ignore_cache = bool(getattr(settings, "IGNORE_CACHE_FOR_EXTRACTION", False))

        # Return from cache if present and not ignored
        cached = None if ignore_cache else (q_cache.get(str(question_num)) or q_cache.get(question_num))
        if cached:
            # If cached is empty/invalid, force re-extraction
            if doc_type == "answer_key" and not cached.get("expert_points"):
                logger.info(f"Cached answer_key Q{question_num} empty; re-extracting.")
            elif doc_type == "student_answers" and not cached.get("student_answer_text"):
                logger.info(f"Cached student_answers Q{question_num} empty; re-extracting.")
            else:
                return cached

        # Extract fresh
        try:
            if doc_type == "answer_key":
                parsed = self._extract_answer_key_fast(full_document_text, question_num) or {}
                if not parsed.get("expert_points"):
                    logger.info(f"Answer key: Q{question_num} not found by fast extractor.")
            else:
                parsed = self._extract_student_answer_fast(full_document_text, question_num) or {}
                if not parsed.get("student_answer_text"):
                    logger.info(f"Student answers: Q{question_num} not found by fast extractor.")

            # Cache and return
            q_cache[str(question_num)] = parsed
            return parsed
        except Exception as e:
            logger.error(f"Extraction error for Q{question_num} ({doc_type}): {e}")
            return {}

    def _select_relevant_points(self, expert_points: List[Dict[str, Any]], student_answer: str, k: int = 5) -> List[Dict[str, Any]]:
        """Use embeddings only to prune prompt size (not for scoring)."""
        if not expert_points:
            return []
        # If SBERT unavailable, just take first K to keep prompt bounded
        if not self.sbert_model or not SENTENCE_TRANSFORMERS_AVAILABLE:
            return expert_points[:max(1, min(k, len(expert_points)))]
        try:
            point_texts = [p["point_text"] for p in expert_points]
            pe_emb = self.sbert_model.encode(point_texts, convert_to_tensor=True, normalize_embeddings=True)
            sa_emb = self.sbert_model.encode([student_answer], convert_to_tensor=True, normalize_embeddings=True)
            sims = util.cos_sim(pe_emb, sa_emb).squeeze(1).tolist()
            ranked = sorted(zip(expert_points, sims), key=lambda x: x[1], reverse=True)
            return [p for p, _ in ranked[:max(1, min(k, len(expert_points)))]]
        except Exception as e:
            logger.warning(f"Point selection failed, using first K points: {e}")
            return expert_points[:max(1, min(k, len(expert_points)))]

    # ---------------------- MAIN EVALUATION METHOD ----------------------
    def evaluate_with_ocr_text(self, answer_key_text: str, student_answer_text: str, max_questions: int = 10) -> Dict:
        """Main evaluation method that processes OCR text and returns results"""
        try:
            logger.info(f"Starting evaluation with max_questions={max_questions}")
            
            # Generate cache keys
            import hashlib
            answer_key_hash = hashlib.md5(answer_key_text.encode()).hexdigest()
            student_hash = hashlib.md5(student_answer_text.encode()).hexdigest()
            answer_key_cache_key = f"answer_key_{hash(answer_key_text)}"
            student_cache_key = f"student_{hash(student_answer_text)}"
            
            detailed_results = []
            total_marks_obtained = 0.0
            total_marks_possible = 0
            
            for qnum in range(1, max_questions + 1):
                logger.info(f"Processing Question {qnum}...")
                
                # Extract answer key for this question
                answer_key_data = self._get_extracted_data_from_cache_or_llm(
                    qnum, answer_key_text, answer_key_cache_key, "answer_key"
                )
                
                if not answer_key_data:
                    logger.warning(f"No answer key found for Q{qnum}")
                    continue
                
                # Extract student answer for this question
                student_data = self._get_extracted_data_from_cache_or_llm(
                    qnum, student_answer_text, student_cache_key, "student_answers"
                )
                
                if not student_data or not student_data.get("student_answer_text"):
                    logger.warning(f"No student answer found for Q{qnum}")
                    continue
                
                # Evaluate this question
                expert_points = answer_key_data.get("expert_points", [])
                if not expert_points:
                    logger.warning(f"No expert points for Q{qnum}")
                    continue
                
                question_text = answer_key_data.get("question_text", f"Question {qnum}")
                student_answer = student_data.get("student_answer_text", "")
                total_marks = answer_key_data.get("total_marks", 5)
                
                # Get LLM evaluation
                eval_result = self._evaluate_answer_pair(
                    question_text, expert_points, student_answer, total_marks
                )
                
                # Calculate marks for this question
                point_evaluations = eval_result.get("point_evaluations", [])
                question_marks = 0.0
                
                for pe in point_evaluations:
                    # Find the corresponding expert point to get its marks
                    expert_point = None
                    for ep in expert_points:
                        if ep["point_text"] == pe.get("expert_point_text", ""):
                            expert_point = ep
                            break
                    
                    if expert_point:
                        point_marks = expert_point.get("point_marks", 1)
                        score = pe.get("score", 0.0)
                        question_marks += score * point_marks
                
                total_marks_obtained += question_marks
                total_marks_possible += total_marks
                
                # Store detailed result
                detailed_results.append({
                    "question_number": qnum,
                    "question": question_text,
                    "student_answer": student_answer,
                    "marks_obtained": round(question_marks, 2),
                    "total_marks": total_marks,
                    "point_evaluations": point_evaluations,
                    "overall_semantic_accuracy": eval_result.get("overall_semantic_accuracy", 0.0),
                    "overall_factual_correctness": eval_result.get("overall_factual_correctness", 0.0),
                    "overall_understanding_depth": eval_result.get("overall_understanding_depth", 0.0),
                    "feedback": eval_result.get("feedback", "")
                })
                
                logger.info(f"Question {qnum}: {question_marks:.1f}/{total_marks} marks")
            
            # Calculate overall statistics
            overall_percentage = (total_marks_obtained / total_marks_possible * 100) if total_marks_possible > 0 else 0
            letter_grade = self.get_letter_grade(overall_percentage)
            
            # Summary
            summary = {
                "total_questions_evaluated": len(detailed_results),
                "total_marks_obtained": round(total_marks_obtained, 2),
                "total_marks_possible": total_marks_possible,
                "overall_percentage": round(overall_percentage, 2),
                "letter_grade": letter_grade
            }
            
            # Save cache
            self._save_cache()
            
            logger.info(f"Evaluation complete: {total_marks_obtained:.1f}/{total_marks_possible} ({overall_percentage:.1f}%)")
            
            return {
                "summary": summary,
                "detailed_results": detailed_results
            }
            
        except Exception as e:
            logger.error(f"Evaluation failed: {e}")
            raise

    def _evaluate_answer_pair(self, question_text: str, expert_points: List[Dict[str, Any]],
                              student_answer: str, total_marks: int) -> Dict:
        # Never use fast path unless FAST_MODE=True
        if self.fast_mode:
            return self._evaluate_answer_pair_fast(question_text, expert_points, student_answer, total_marks)

        self._ensure_llm()

        # USE ALL POINTS (remove pruning)
        pruned_points = expert_points  # no pruning to avoid losing marks

        expert_points_formatted = "\n".join([f"- {p['point_text']}" for p in pruned_points])

        def build_prompt(compact: bool = False) -> str:
            schema = (
                '{"point_evaluations":[{"expert_point_text":"...","score":0.0,'
                '"feedback_on_point":"..."}],"overall_semantic_accuracy":0.0,'
                '"overall_factual_correctness":0.0,"overall_understanding_depth":0.0,'
                '"feedback":"..."}'
            )
            instr = (
                "Return ONLY a valid JSON object. Use double quotes for all keys and strings. "
                "No code fences. No prose outside JSON. Keys exactly as in the schema. "
                "Use the expert point texts EXACTLY as provided (copy verbatim). "
                "Return point_evaluations in the SAME ORDER and SAME LENGTH as the expert points list."
            )
            if compact:
                return (
                    f"{instr}\nSchema: {schema}\n"
                    f"Question: {question_text}\n"
                    f"Expert Points (use exact texts):\n{expert_points_formatted}\n"
                    f"Student Answer:\n{student_answer}\n"
                )
            return (
                "You are an expert examiner. Evaluate the student's answer against the expert points.\n"
                f"{instr}\nSchema: {schema}\n\n"
                f"Question:\n{question_text}\n\nExpert Points (use exact texts):\n{expert_points_formatted}\n\n"
                f"Student Answer:\n{student_answer}\n"
            )

        prompts = [build_prompt(False), build_prompt(True)]
        data = None
        for attempt, prompt in enumerate(prompts, start=1):
            t0 = time.time()
            raw = self.generate_llm_response(
                prompt,
                max_tokens=settings.MAX_NEW_TOKENS_EVAL,
                temperature=0.0,
                timeout_seconds=settings.GEN_TIMEOUT_EVAL,
                input_truncate_tokens=settings.INPUT_TOKENS_LIMIT_EVAL
            )
            logger.info(f"LLM eval attempt {attempt} time: {time.time()-t0:.1f}s; len={len(raw)}")
            logger.info(f"Raw LLM response (first 400): {raw[:400]}")
            if not raw.strip():
                continue
            try:
                raw_clean = self._sanitize_json_response(raw)
                data = json.loads(raw_clean)
                break
            except Exception as e:
                # Try regex recovery for point_evaluations
                logger.warning(f"JSON parse failed after sanitize (attempt {attempt}): {e}")
                recovered = self._extract_point_evals_from_text(raw)
                if recovered:
                    data = {
                        "point_evaluations": recovered,
                        "overall_semantic_accuracy": 0.0,
                        "overall_factual_correctness": 0.0,
                        "overall_understanding_depth": 0.0,
                        "feedback": ""
                    }
                    logger.info(f"Recovered {len(recovered)} point_evaluations via regex")
                    break
                continue
        else:
            # Final JSON-minified retry
            mini_prompt = (
                '{"point_evaluations":[{"expert_point_text":"...","score":0.0,"feedback_on_point":"..."}],'
                '"overall_semantic_accuracy":0.0,"overall_factual_correctness":0.0,'
                '"overall_understanding_depth":0.0,"feedback":"..."}\n'
                f'Fill this JSON. Use exact expert point texts. Return only minified JSON.\n'
                f'Q: {question_text}\nPoints:\n{expert_points_formatted}\nAnswer:\n{student_answer}\n'
            )
            raw = self.generate_llm_response(mini_prompt, max_tokens=settings.MAX_NEW_TOKENS_EVAL, temperature=0.0,
                                             timeout_seconds=settings.GEN_TIMEOUT_EVAL,
                                             input_truncate_tokens=settings.INPUT_TOKENS_LIMIT_EVAL)
            try:
                data = json.loads(self._sanitize_json_response(raw or ""))
            except Exception:
                # TSV MODE fallback: index-based lines -> parse robustly
                tsv_prompt = (
                    "Return ONLY TSV with three columns per line: index (1-based)\tscore (0..1)\tfeedback.\n"
                    "One line per expert point IN THE SAME ORDER as provided. No extra text.\n"
                    f"Question:\n{question_text}\n\nExpert Points (indexed 1..N):\n"
                    + "\n".join([f"{i+1}. {p['point_text']}" for i,p in enumerate(pruned_points)]) +
                    f"\n\nStudent Answer:\n{student_answer}\n"
                )
                raw_tsv = self.generate_llm_response(tsv_prompt, max_tokens=256, temperature=0.0,
                                                      timeout_seconds=settings.GEN_TIMEOUT_EVAL,
                                                      input_truncate_tokens=settings.INPUT_TOKENS_LIMIT_EVAL)
                rows = self._parse_tsv_scores(raw_tsv or "", pruned_points)
                if rows:
                    data = {
                        "point_evaluations": rows,
                        "overall_semantic_accuracy": 0.0,
                        "overall_factual_correctness": 0.0,
                        "overall_understanding_depth": 0.0,
                        "feedback": ""
                    }
                else:
                    return {
                        "point_evaluations": [
                            {"expert_point_text": p["point_text"], "score": 0.0, "feedback_on_point": "LLM did not return valid JSON"}
                            for p in expert_points
                        ],
                        "overall_semantic_accuracy": 0.0,
                        "overall_factual_correctness": 0.0,
                        "overall_understanding_depth": 0.0,
                        "feedback": "Evaluation failed: LLM response invalid or empty"
                    }

        # Fuzzy matching helper (normalized)
        def _normalize_text(s: str) -> str:
            s = (s or "").lower()
            s = re.sub(r'[\[\](){}]', ' ', s)
            s = re.sub(r'\b\d+\.\s*$', '', s)       # strip trailing enumerators like "2."
            s = re.sub(r'^\s*\d+\s*[\).:-]\s*', '', s)  # strip leading "1." / "1)" / "1:"
            s = re.sub(r'\s+', ' ', s).strip(' .-,:;')
            return s

        def _find_match(llm_text: str, used_idxs: set[int]) -> int | None:
            lt = _normalize_text(llm_text)
            best_idx = None
            best_score = 0.0
            for idx, ep in enumerate(expert_points):
                if idx in used_idxs:
                    continue
                et = _normalize_text(ep["point_text"])
                if lt and (lt in et or et in lt):
                    ratio = 1.0
                else:
                    ratio = SequenceMatcher(None, lt, et).ratio()
                if ratio > best_score:
                    best_score = ratio
                    best_idx = idx
            return best_idx if (best_idx is not None and best_score >= 0.55) else None

        # Normalize point evaluations
        llm_point_evals = data.get("point_evaluations", [])
        # First pass: fuzzy map each LLM eval to the best expert point not yet used
        idx_for_llm: list[int | None] = []
        used_idxs: set[int] = set()
        for pe in llm_point_evals:
            raw_text = pe.get("expert_point_text", "")
            # exact first on normalized text
            exact_idx = None
            lt_norm = _normalize_text(raw_text)
            for i, ep in enumerate(expert_points):
                if i in used_idxs:
                    continue
                if _normalize_text(ep["point_text"]) == lt_norm and lt_norm:
                    exact_idx = i
                    break
            if exact_idx is not None:
                idx_for_llm.append(exact_idx)
                used_idxs.add(exact_idx)
                continue
            # fuzzy
            best_idx = _find_match(raw_text, used_idxs)
            idx_for_llm.append(best_idx)
            if best_idx is not None:
                used_idxs.add(best_idx)

        # Second pass: order-based fill for any remaining unmatched items
        # Map remaining LLM items to first unmatched expert points in order
        unmatched_expert_idxs = [i for i in range(len(expert_points)) if i not in used_idxs]
        for k, mapped in enumerate(idx_for_llm):
            if mapped is None and unmatched_expert_idxs:
                idx_for_llm[k] = unmatched_expert_idxs.pop(0)

        # Build evaluations in expert order to ensure consistent UI/marking
        normalized_evals: List[Dict[str, Any]] = []
        # Prepare a lookup from expert idx -> (score, fb) from first mapped LLM item
        mapped_by_expert: dict[int, Dict[str, Any]] = {}
        for pe, idx in zip(llm_point_evals, idx_for_llm):
            if idx is None or idx in mapped_by_expert:
                continue
            try:
                score = max(0.0, min(1.0, float(pe.get("score", 0.0))))
            except Exception:
                score = 0.0
            mapped_by_expert[idx] = {
                "expert_point_text": expert_points[idx]["point_text"],
                "score": score,
                "feedback_on_point": pe.get("feedback_on_point", "")
            }

        matched_ids = set()
        for idx, ep in enumerate(expert_points):
            if idx in mapped_by_expert:
                normalized_evals.append(mapped_by_expert[idx])
                matched_ids.add(ep["point_text"])
            else:
                normalized_evals.append({
                    "expert_point_text": ep["point_text"],
                    "score": 0.0,
                    "feedback_on_point": "Not evaluated"
                })

        # Recompute overall metrics from normalized_evals
        if normalized_evals:
            avg_score = sum(pe["score"] for pe in normalized_evals) / len(normalized_evals)
        else:
            avg_score = 0.0
        coverage = sum(1 for pe in normalized_evals if pe["score"] >= 0.5) / max(1, len(normalized_evals))
        understanding = (avg_score + coverage) / 2.0

        data["point_evaluations"] = normalized_evals
        data["overall_semantic_accuracy"] = avg_score
        data["overall_factual_correctness"] = avg_score
        data["overall_understanding_depth"] = understanding
        return data

    def _evaluate_answer_pair_fast(self, question_text: str, expert_points: List[Dict[str, Any]],
                                   student_answer: str, total_marks: int) -> Dict:
        """Fallback fast scoring. If SBERT unavailable, return zeros with clear message."""
        if not self.sbert_model or not SENTENCE_TRANSFORMERS_AVAILABLE:
            return {
                "point_evaluations": [{"expert_point_text": p["point_text"], "score": 0.0, "feedback_on_point": ""} for p in expert_points],
                "overall_semantic_accuracy": 0.0,
                "overall_factual_correctness": 0.0,
                "overall_understanding_depth": 0.0,
                "feedback": "Semantic fallback disabled (sentence-transformers not available)."
            }
        point_texts = [p["point_text"] for p in expert_points]
        student_emb = self.sbert_model.encode([student_answer], convert_to_tensor=True, normalize_embeddings=True)
        sims = []
        if point_texts:
            points_emb = self.sbert_model.encode(point_texts, convert_to_tensor=True, normalize_embeddings=True)
            sims = util.cos_sim(points_emb, student_emb).squeeze(1).tolist()

        point_evals = []
        for p, sim in zip(expert_points, sims):
            score = max(0.0, min(1.0, (float(sim) + 1.0) / 2.0))
            point_evals.append({
                "expert_point_text": p["point_text"],
                "score": score,
                "feedback_on_point": ""
            })

        coverage = sum(1 for pe in point_evals if pe["score"] > 0.5) / max(1, len(point_evals)) if point_evals else 0.0
        overall_semantic_accuracy = float(sum(pe["score"] for pe in point_evals) / max(1, len(point_evals))) if point_evals else 0.0
        overall_factual_correctness = overall_semantic_accuracy
        overall_understanding_depth = (overall_semantic_accuracy + coverage) / 2.0

        return {
            "point_evaluations": point_evals,
            "overall_semantic_accuracy": overall_semantic_accuracy,
            "overall_factual_correctness": overall_factual_correctness,
            "overall_understanding_depth": overall_understanding_depth,
            "feedback": "Fast evaluation based on semantic similarity (no LLM). Higher similarity indicates better coverage of key points."
        }

    def get_letter_grade(self, percentage: float) -> str:
        """Convert percentage to letter grade"""
        if percentage >= 97: return 'A+'
        elif percentage >= 93: return 'A'
        elif percentage >= 90: return 'A-'
        elif percentage >= 87: return 'B+'
        elif percentage >= 83: return 'B'
        elif percentage >= 80: return 'B-'
        elif percentage >= 77: return 'C+'
        elif percentage >= 73: return 'C'
        elif percentage >= 70: return 'C-'
        elif percentage >= 60: return 'D'
        else: return 'F'

    def _hf_chat_completion_via_client(self, model_name: str, prompt_text: str,
                                       max_tokens: int, temperature: float, timeout_seconds: int) -> str:
        """Chat completion via HF InferenceClient (OpenAI-compatible)."""
        try:
            def _do():
                completion = self.hf_client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt_text}],
                    max_tokens=int(max_tokens),
                    temperature=float(temperature) if temperature else 0.0,
                    stop=["```", "\n```"]  # reduce trailing prose
                )
                choice = (completion.choices or [None])[0]
                msg = getattr(choice, "message", None)
                return (getattr(msg, "content", "") or "").strip()
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_do)
                return fut.result(timeout=timeout_seconds) or ""
        except FuturesTimeoutError:
            logger.error(f"HF chat.completions timed out after {timeout_seconds}s for model={model_name}")
            return ""
        except Exception as e:
            logger.error(f"HF chat.completions error for model={model_name}: {e}")
            return ""

    def _hf_text_generation(self, model_name: str, prompt_text: str, max_tokens: int,
                            temperature: float) -> str:
        """Fallback: text_generation for non-chat models only."""
        try:
            client = InferenceClient(token=settings.HF_TOKEN)
            def _do():
                out = client.text_generation(
                    model=model_name,
                    prompt=prompt_text,
                    max_new_tokens=int(max_tokens),
                    temperature=float(temperature) if temperature else 0.0,
                    do_sample=bool(temperature and temperature > 0),
                    return_full_text=False,
                )
                return (out or "").strip()
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_do)
                return fut.result(timeout=settings.GEN_TIMEOUT_EVAL) or ""
        except FuturesTimeoutError:
            logger.error(f"HF Inference timed out for model={model_name}")
            return ""
        except Exception as e:
            logger.error(f"HF Inference error for model={model_name}: {e}")
            return ""

    def _split_student_blocks(self, text: str) -> List[str]:
        """Split student OCR text into sequential question blocks using broad heading patterns."""
        cleaned = self._clean_text(text)
        lines = cleaned.split('\n')

        # Accept Qn, Question n, numeric-only like "1.", "(1)", "1)" and common OCR confusions (O/0 for Q)
        pats = [
            re.compile(r'^(?:#+\s*)?Q(?:uestion)?\s*\d+\s*[).: -]+', re.IGNORECASE),  # Q1. / Question 1:
            re.compile(r'^(?:#+\s*)?[QO0]\s*\d+\s*[).: -]+'),                         # OCR: O1. / 01.
            re.compile(r'^\s*\d+\s*[).:]\s*$', re.IGNORECASE),                        # "42." on its own line
            re.compile(r'^\s*\(\s*\d+\s*\)\s*$', re.IGNORECASE),                      # "(42)"
            re.compile(r'^\s*\d+\s*[):-]\s+', re.IGNORECASE),                         # "42) text" / "42: text"
        ]

        starters = []
        for i, ln in enumerate(lines):
            if any(p.match(ln) for p in pats):
                starters.append(i)

        if not starters:
            return [cleaned] if cleaned.strip() else []

        blocks = []
        for idx, si in enumerate(starters):
            ei = starters[idx + 1] if idx + 1 < len(starters) else len(lines)
            block = '\n'.join(lines[si:ei]).strip()
            if len(block) >= 30:
                blocks.append(block)
        return blocks

    def _sanitize_and_parse_json(self, raw_response: str) -> dict:
        """Enhanced JSON parsing with truncation recovery and better cleanup."""
        if not raw_response:
            return {}

        # Remove common prefixes/suffixes
        response = raw_response.strip()
        response = re.sub(r'^[^{]*', '', response)  # Remove everything before first {
        
        # Try parsing as-is first
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Find the JSON object boundaries
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if not json_match:
            logger.warning("No JSON object found in response")
            return {}
        
        json_str = json_match.group(0)
        
        # Try parsing the extracted JSON
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error: {e}")
            
            # Attempt to fix common truncation issues
            fixed_json = self._fix_truncated_json(json_str)
            try:
                return json.loads(fixed_json)
            except json.JSONDecodeError:
                logger.warning("Failed to fix truncated JSON, using regex fallback")
                return self._extract_evaluations_regex(response)

    def _fix_truncated_json(self, json_str: str) -> str:
        """Attempt to fix truncated JSON by closing incomplete structures."""
        # Count braces and brackets to detect truncation
        open_braces = json_str.count('{') - json_str.count('}')
        open_brackets = json_str.count('[') - json_str.count(']')
        
        # If we have unclosed structures, try to close them
        if open_braces > 0 or open_brackets > 0:
            # Remove any incomplete trailing content
            json_str = re.sub(r',\s*$', '', json_str)  # Remove trailing comma
            json_str = re.sub(r'[^}\]]*$', '', json_str)  # Remove incomplete content at end
            
            # Close missing brackets and braces
            json_str += ']' * open_brackets
            json_str += '}' * open_braces
            
        return json_str

    def _extract_evaluations_regex(self, response: str) -> dict:
        """Fallback: extract point evaluations using regex when JSON parsing fails."""
        logger.info("Using regex fallback for point evaluation extraction")
        
        # Pattern to match point evaluations in various formats
        patterns = [
            # Standard format: "expert_point_text": "text", "score": 0.5, "feedback_on_point": "feedback"
            r'"expert_point_text"\s*:\s*"([^"]*)"[^}]*?"score"\s*:\s*([0-9.]+)[^}]*?"feedback_on_point"\s*:\s*"([^"]*)"',
            # Alternative format without quotes around score
            r'"expert_point_text"\s*:\s*"([^"]*)"[^}]*?score\s*:\s*([0-9.]+)[^}]*?"feedback_on_point"\s*:\s*"([^"]*)"',
        ]
        
        evaluations = []
        
        for pattern in patterns:
            matches = re.findall(pattern, response, re.IGNORECASE | re.DOTALL)
            for match in matches:
                try:
                    score = max(0.0, min(1.0, float(match[1])))
                    evaluations.append({
                        "expert_point_text": match[0].strip(),
                        "score": score,
                        "feedback_on_point": match[2].strip()
                    })
                except (ValueError, IndexError):
                    continue
        
        if evaluations:
            logger.info(f"Recovered {len(evaluations)} point_evaluations via regex")
            return {
                "point_evaluations": evaluations,
                "overall_semantic_accuracy": sum(e["score"] for e in evaluations) / len(evaluations),
                "overall_factual_correctness": sum(e["score"] for e in evaluations) / len(evaluations),
                "overall_understanding_depth": sum(e["score"] for e in evaluations) / len(evaluations),
                "feedback": "Evaluation completed with regex fallback due to JSON parsing issues."
            }
        
        return {}

    def _evaluate_answer_pair(self, question_text: str, expert_points: List[Dict[str, Any]],
                              student_answer: str, total_marks: int) -> Dict:
        # Never use fast path unless FAST_MODE=True
        if self.fast_mode:
            return self._evaluate_answer_pair_fast(question_text, expert_points, student_answer, total_marks)

        self._ensure_llm()

        # USE ALL POINTS (remove pruning)
        pruned_points = expert_points  # no pruning to avoid losing marks

        expert_points_formatted = "\n".join([f"- {p['point_text']}" for p in pruned_points])

        def build_prompt(compact: bool = False) -> str:
            schema = (
                '{"point_evaluations":[{"expert_point_text":"...","score":0.0,'
                '"feedback_on_point":"..."}],"overall_semantic_accuracy":0.0,'
                '"overall_factual_correctness":0.0,"overall_understanding_depth":0.0,'
                '"feedback":"..."}'
            )
            instr = (
                "Return ONLY a valid JSON object. Use double quotes for all keys and strings. "
                "No code fences. No prose outside JSON. Keys exactly as in the schema. "
                "Use the expert point texts EXACTLY as provided (copy verbatim). "
                "Return point_evaluations in the SAME ORDER and SAME LENGTH as the expert points list."
            )
            prompt = f"""Evaluate this student answer against expert points. Return JSON only:

Question: {question_text}

Expert Points:
{chr(10).join(f"{i+1}. {ep['point_text']} ({ep['point_marks']} marks)" for i, ep in enumerate(expert_points))}

Student Answer: {student_answer}

Return:
{{"point_evaluations":[{{"expert_point_text":"point text","score":0.0-1.0,"feedback_on_point":"brief feedback"}}],"overall_semantic_accuracy":0.0-1.0,"overall_factual_correctness":0.0-1.0,"overall_understanding_depth":0.0-1.0,"feedback":"overall feedback"}}

Each score 0.0-1.0. Be concise."""
            if compact:
                return (
                    f"{instr}\nSchema: {schema}\n"
                    f"Question: {question_text}\n"
                    f"Expert Points (use exact texts):\n{expert_points_formatted}\n"
                    f"Student Answer:\n{student_answer}\n"
                )
            return (
                "You are an expert examiner. Evaluate the student's answer against the expert points.\n"
                f"{instr}\nSchema: {schema}\n\n"
                f"Question:\n{question_text}\n\nExpert Points (use exact texts):\n{expert_points_formatted}\n\n"
                f"Student Answer:\n{student_answer}\n"
            )

        prompts = [build_prompt(False), build_prompt(True)]
        data = None
        for attempt, prompt in enumerate(prompts, start=1):
            t0 = time.time()
            raw = self.generate_llm_response(
                prompt,
                max_tokens=settings.MAX_NEW_TOKENS_EVAL,
                temperature=0.0,
                timeout_seconds=settings.GEN_TIMEOUT_EVAL,
                input_truncate_tokens=settings.INPUT_TOKENS_LIMIT_EVAL
            )
            logger.info(f"LLM eval attempt {attempt} time: {time.time()-t0:.1f}s; len={len(raw)}")
            logger.info(f"Raw LLM response (first 400): {raw[:400]}")
            if not raw.strip():
                continue
            try:
                raw_clean = self._sanitize_json_response(raw)
                data = json.loads(raw_clean)
                break
            except Exception as e:
                # Try regex recovery for point_evaluations
                logger.warning(f"JSON parse failed after sanitize (attempt {attempt}): {e}")
                recovered = self._extract_point_evals_from_text(raw)
                if recovered:
                    data = {
                        "point_evaluations": recovered,
                        "overall_semantic_accuracy": 0.0,
                        "overall_factual_correctness": 0.0,
                        "overall_understanding_depth": 0.0,
                        "feedback": ""
                    }
                    logger.info(f"Recovered {len(recovered)} point_evaluations via regex")
                    break
                continue
        else:
            # Final JSON-minified retry
            mini_prompt = (
                '{"point_evaluations":[{"expert_point_text":"...","score":0.0,"feedback_on_point":"..."}],'
                '"overall_semantic_accuracy":0.0,"overall_factual_correctness":0.0,'
                '"overall_understanding_depth":0.0,"feedback":"..."}\n'
                f'Fill this JSON. Use exact expert point texts. Return only minified JSON.\n'
                f'Q: {question_text}\nPoints:\n{expert_points_formatted}\nAnswer:\n{student_answer}\n'
            )
            raw = self.generate_llm_response(mini_prompt, max_tokens=settings.MAX_NEW_TOKENS_EVAL, temperature=0.0,
                                             timeout_seconds=settings.GEN_TIMEOUT_EVAL,
                                             input_truncate_tokens=settings.INPUT_TOKENS_LIMIT_EVAL)
            try:
                data = json.loads(self._sanitize_json_response(raw or ""))
            except Exception:
                # TSV MODE fallback: index-based lines -> parse robustly
                tsv_prompt = (
                    "Return ONLY TSV with three columns per line: index (1-based)\tscore (0..1)\tfeedback.\n"
                    "One line per expert point IN THE SAME ORDER as provided. No extra text.\n"
                    f"Question:\n{question_text}\n\nExpert Points (indexed 1..N):\n"
                    + "\n".join([f"{i+1}. {p['point_text']}" for i,p in enumerate(pruned_points)]) +
                    f"\n\nStudent Answer:\n{student_answer}\n"
                )
                raw_tsv = self.generate_llm_response(tsv_prompt, max_tokens=256, temperature=0.0,
                                                      timeout_seconds=settings.GEN_TIMEOUT_EVAL,
                                                      input_truncate_tokens=settings.INPUT_TOKENS_LIMIT_EVAL)
                rows = self._parse_tsv_scores(raw_tsv or "", pruned_points)
                if rows:
                    data = {
                        "point_evaluations": rows,
                        "overall_semantic_accuracy": 0.0,
                        "overall_factual_correctness": 0.0,
                        "overall_understanding_depth": 0.0,
                        "feedback": ""
                    }
                else:
                    return {
                        "point_evaluations": [
                            {"expert_point_text": p["point_text"], "score": 0.0, "feedback_on_point": "LLM did not return valid JSON"}
                            for p in expert_points
                        ],
                        "overall_semantic_accuracy": 0.0,
                        "overall_factual_correctness": 0.0,
                        "overall_understanding_depth": 0.0,
                        "feedback": "Evaluation failed: LLM response invalid or empty"
                    }

        # Fuzzy matching helper (normalized)
        def _normalize_text(s: str) -> str:
            s = (s or "").lower()
            s = re.sub(r'[\[\](){}]', ' ', s)
            s = re.sub(r'\b\d+\.\s*$', '', s)       # strip trailing enumerators like "2."
            s = re.sub(r'^\s*\d+\s*[\).:-]\s*', '', s)  # strip leading "1." / "1)" / "1:"
            s = re.sub(r'\s+', ' ', s).strip(' .-,:;')
            return s

        def _find_match(llm_text: str, used_idxs: set[int]) -> int | None:
            lt = _normalize_text(llm_text)
            best_idx = None
            best_score = 0.0
            for idx, ep in enumerate(expert_points):
                if idx in used_idxs:
                    continue
                et = _normalize_text(ep["point_text"])
                if lt and (lt in et or et in lt):
                    ratio = 1.0
                else:
                    ratio = SequenceMatcher(None, lt, et).ratio()
                if ratio > best_score:
                    best_score = ratio
                    best_idx = idx
            return best_idx if (best_idx is not None and best_score >= 0.55) else None

        # Normalize point evaluations
        llm_point_evals = data.get("point_evaluations", [])
        # First pass: fuzzy map each LLM eval to the best expert point not yet used
        idx_for_llm: list[int | None] = []
        used_idxs: set[int] = set()
        for pe in llm_point_evals:
            raw_text = pe.get("expert_point_text", "")
            # exact first on normalized text
            exact_idx = None
            lt_norm = _normalize_text(raw_text)
            for i, ep in enumerate(expert_points):
                if i in used_idxs:
                    continue
                if _normalize_text(ep["point_text"]) == lt_norm and lt_norm:
                    exact_idx = i
                    break
            if exact_idx is not None:
                idx_for_llm.append(exact_idx)
                used_idxs.add(exact_idx)
                continue
            # fuzzy
            best_idx = _find_match(raw_text, used_idxs)
            idx_for_llm.append(best_idx)
            if best_idx is not None:
                used_idxs.add(best_idx)

        # Second pass: order-based fill for any remaining unmatched items
        # Map remaining LLM items to first unmatched expert points in order
        unmatched_expert_idxs = [i for i in range(len(expert_points)) if i not in used_idxs]
        for k, mapped in enumerate(idx_for_llm):
            if mapped is None and unmatched_expert_idxs:
                idx_for_llm[k] = unmatched_expert_idxs.pop(0)

        # Build evaluations in expert order to ensure consistent UI/marking
        normalized_evals: List[Dict[str, Any]] = []
        # Prepare a lookup from expert idx -> (score, fb) from first mapped LLM item
        mapped_by_expert: dict[int, Dict[str, Any]] = {}
        for pe, idx in zip(llm_point_evals, idx_for_llm):
            if idx is None or idx in mapped_by_expert:
                continue
            try:
                score = max(0.0, min(1.0, float(pe.get("score", 0.0))))
            except Exception:
                score = 0.0
            mapped_by_expert[idx] = {
                "expert_point_text": expert_points[idx]["point_text"],
                "score": score,
                "feedback_on_point": pe.get("feedback_on_point", "")
            }

        matched_ids = set()
        for idx, ep in enumerate(expert_points):
            if idx in mapped_by_expert:
                normalized_evals.append(mapped_by_expert[idx])
                matched_ids.add(ep["point_text"])
            else:
                normalized_evals.append({
                    "expert_point_text": ep["point_text"],
                    "score": 0.0,
                    "feedback_on_point": "Not evaluated"
                })

        # Recompute overall metrics from normalized_evals
        if normalized_evals:
            avg_score = sum(pe["score"] for pe in normalized_evals) / len(normalized_evals)
        else:
            avg_score = 0.0
        coverage = sum(1 for pe in normalized_evals if pe["score"] >= 0.5) / max(1, len(normalized_evals))
        understanding = (avg_score + coverage) / 2.0

        data["point_evaluations"] = normalized_evals
        data["overall_semantic_accuracy"] = avg_score
        data["overall_factual_correctness"] = avg_score
        data["overall_understanding_depth"] = understanding
        return data
