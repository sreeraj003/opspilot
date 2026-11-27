"""Natural language to SQL generation (Module 10).

Turns a validated ``BusinessIntent`` (Module 09) into one **untrusted**
candidate SQL query for the registered schema's dialect. It never executes SQL,
never calls the company connector and never approves a query: the SQL security
guard (Module 11) must validate and authorize it independently.

Sub-packages:

* ``schemas``      ``SQLGenerationResult``, the model's output contract, API models
* ``dialects``     supported dialects (PostgreSQL, MySQL, SQL Server) and their conventions
* ``context``      the constrained, permission-aware generation context
* ``prompts``      the versioned system prompt and regeneration feedback
* ``normalizers``  strict reply extraction, SQL rendering and fingerprinting
* ``validators``   preliminary SQLGlot validation
* ``services``     ``SQLGenerationService`` (orchestration, persistence, audit)
"""
