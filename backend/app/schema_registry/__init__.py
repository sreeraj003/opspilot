"""Schema registry domain logic (Module 06), independent of HTTP and persistence.

* ``data_types``: identifier rules, supported data types and their categories.
* ``validation``: the schema validation service (structured issues).
* ``importer``: JSON import planning (create_only / merge / replace).
* ``representation``: the AI-ready schema representation.
* ``catalog``: the ``SchemaCatalog`` that permission policies validate against.
* ``json_limits``: size and nesting limits for untrusted JSON documents.

``app.services.schema_registry_service`` combines these with the repositories.
"""
