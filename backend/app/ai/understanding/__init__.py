"""Question understanding and business intent extraction (Module 09).

Turns a user's natural-language business question into a validated,
structured ``BusinessIntent``. It never generates or executes SQL, never calls
the company connector and never decides authorization: the LLM interprets,
deterministic backend code validates, resolves dates and checks permissions.

Sub-packages:

* ``schemas``      ``BusinessIntent`` and its parts, the LLM output contract, API models
* ``validators``   executable-content detection and independent validation of LLM output
* ``normalizers``  deterministic time resolution, text normalisation, JSON extraction
* ``prompts``      the constrained system prompt and context payload
* ``services``     schema relevance, terminology, permission view, clarification, orchestration
"""
