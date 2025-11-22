The evaluation of handwritten answer scripts is a persistent challenge in academic
institutions due to its time-consuming nature, susceptibility to bias, and lack of
scalability. This project presents a modernized, production-grade, AI-driven
evaluation system capable of automatically analyzing student answers submitted
as PDFs or images using a multi-modal pipeline combining OCR, NLP, and
Large Language Models (LLMs).
This system features a complete full-stack architecture incorporating a Re-
act (Vite) frontend, a FastAPI backend, multi-engine OCR services,
HuggingFace-based LLM evaluation, and a sophisticated diagram eval-
uation module powered by Qwen2.5-VL.
The system supports complex real-world academic documents, including scanned
PDFs, handwritten answers, and diagram-based responses. OCR extraction is han-
dled through a multi-strategy engine using Tesseract, EasyOCR, or Mistral
OCR. Extracted content undergoes advanced parsing to identify question bound-
aries, marks distribution, and expert key points. The core evaluation leverages
LLM-based semantic scoring, delivering point-wise evaluation, factual correct-
ness analysis, conceptual understanding scores, and structured JSON responses.
Diagram-based questions are evaluated using vision-language prompting, en-
abling comparisons between student and answer-key diagrams.
The architecture is production-ready with support for caching, fallback chains,
asynchronous processing, error-handling, multi-modal prompts, and real-time progress
updates—making it suitable for institutional deployment.
This report presents a robust end-to-end solution that reflects current advance-
ments in AI and software engineering.
