#==========================================================================
# Minimal Soliplex installation configuration
#
# Please See the corresponding sections 'example/installation.yaml' for
# descriptions of the defaults for sections not configured here.
#==========================================================================

id: "${setup_id}"

#==========================================================================
# Meta-configuration
#==========================================================================
# See: https://soliplex.github.io/soliplex/config/meta/
#==========================================================================
meta:

  #------------------------------------------------------------------------
  # Register AG-UI feature models so that they can be referenced by
  # their 'name'
  #
  # 'haiku.rag.agents.chat.state.ChatSessionState' is registered by
  # default, just as if we configured here:
  #
  # agui_features:
  #
  # - name: "haiku.rag.chat"
  #   model_klass: "haiku.rag.agents.chat.state.ChatSessionState"
  #   source: "server"
  #------------------------------------------------------------------------

  #------------------------------------------------------------------------
  # Register tool configuration types so that they can be referenced by
  # their 'tool_name'
  #
  # Example (assuming you have added a `config` module with a custom
  # tool configuration):
  #
  # tool_configs:
  # - "${package_name}.config.MyToolConfig"
  #------------------------------------------------------------------------

  #------------------------------------------------------------------------
  # Register MCP client toolset configuration types so that they can be
  # referenced by their 'kind'.
  #
  # 'soliplex.config.Stdio_MCP_ClientToolsetConfig' and
  # 'soliplex.config.HTTP_MCP_ClientToolsetConfig' are registered by default,
  # just as if we configured here:
  #
  # mcp_toolset_configs:
  # - "soliplex.config.Stdio_MCP_ClientToolsetConfig"
  # - "soliplex.config.HTTP_MCP_ClientToolsetConfig"
  #------------------------------------------------------------------------

  #------------------------------------------------------------------------
  # Register skill configuration types so that they can be referenced by
  # their 'kind'
  #
  # 'soliplex.config.HR_RAG_SkillConfig' and
  # 'soliplex.config.HR_RLM_SkillConfig' are registered by default,
  # just as if we configured here:
  #
  # skill_configs:
  # - "soliplex.config.HR_RAG_SkillConfig"
  # - "soliplex.config.HR_RLM_SkillConfig"
  #------------------------------------------------------------------------

  #------------------------------------------------------------------------
  # Register MCP server tool wrapper types so that they can be used to
  # wrap tools from a given tool configuration class.
  #
  # Example (assuming you have added a `config` module with a custom
  # tool configuration and wrapper class):
  #
  # mcp_server_tool_wrappers:
  # - config_klass: "${package_name}.config.MyToolConfig"
  #   wrapper_klass: "${package_name}.config.MyMCPWrapper"
  #------------------------------------------------------------------------

  #------------------------------------------------------------------------
  # Register agent configuration types so that they can be referenced by
  # their 'kind'
  #
  # 'soliplex.config.AgentConfig' and 'soliplex.config.FactoryAgentConfig'
  # are registered by default, just as if we configured here:
  #
  # agent_configs:
  # - "soliplex.config.AgentConfig"
  # - "soliplex.config.FactoryAgentConfig"
  #------------------------------------------------------------------------

  #------------------------------------------------------------------------
  # Register implementation functions for different secret source classes
  #
  # By default, these classes are configured to use the corresponding
  # functions in 'soliplex.secrets', just as if we configured here:
  #
  # secret_sources:
  # - config_klass: "soliplex.config.EnvVarSecretSource"
  #   registered_func: "soliplex.secrets.get_env_var_secret"
  # - config_klass: "soliplex.config.FilePathSecretSource"
  #   registered_func: "soliplex.secrets.get_file_path_secret"
  # - config_klass: "soliplex.config.SubprocessSecretSource"
  #   registered_func: "soliplex.secrets.get_subprocess_secret"
  # - config_klass: "soliplex.config.RandomCharsSecretSource"
  #   registered_func: "soliplex.secrets.get_random_chars_secret"
  #------------------------------------------------------------------------

#==========================================================================
# FastAPI routers
#==========================================================================
# Routers from these Soliplex view modules are registered by default,
#
# - 'soliplex.views'
# - 'soliplex.views.agui'
# - 'soliplex.views.authn'
# - 'soliplex.views.authz'
# - 'soliplex.views.completions'
# - 'soliplex.views.installation'
# - 'soliplex.views.log_ingest'
# - 'soliplex.views.quizzes'
# - 'soliplex.views.rooms'
#
# just as if we configured here:
#
# app_router_operations:
#    - kind: "clear"
#    - kind: "add"
#      group_name: "views"
#      router_name: 'soliplex.views.router'
#      prefix: "/api"
#    ...
#    - kind: "add"
#      grouop_name: "rooms"
#      router_name: 'soliplex.views.agui.router'
#      prefix: "/api"
#
# To add a new router without clearing the defaults, e.g. the router
# from 'soliplex.views.streaming', which is not configured by default:
#
# app_router_operations:
#    - kind: "add"
#      group_name: "streaming"
#      router_name: 'soliplex.views.streaming.router'
#      prefix: "/api"
#
# To remove a router which is configured by, e.g. the router
# from 'soliplex.views.completions':
#
# app_router_operations:
#    - kind: "delete"
#      group_name: "completions"
#==========================================================================

#==========================================================================
# Secrets
#==========================================================================
# See: https://soliplex.github.io/soliplex/config/secrets/
#
# NOTE This configuration does not wire up any external APIs / MCP client
# tools, and therefore requires no secrets beyond the one defined below,
# which gets auto-populated at startup if it is not found in the environment.
#==========================================================================

secrets:
  # ----------------------------------------------------------------------
  # AGUI thread persistence DB
  # ----------------------------------------------------------------------
  - secret_name: "AGUI_DB_PASSWORD"
    sources:
      - kind: "file_path"
        file_path: /run/secrets/agui_db_password

  # ----------------------------------------------------------------------
  # Authz thread persistence DB
  # ----------------------------------------------------------------------
  - secret_name: "AUTHZ_DB_PASSWORD"
    sources:
      - kind: "file_path"
        file_path: /run/secrets/authz_db_password

  # ----------------------------------------------------------------------
  # MCP room token generation / validation secret
  #
  # If not set in the environment, we generate a random value at startup,
  # which will invalidate any previously-generated MCP room tokens.
  #
  # To generate:
  #
  # $ python3 -c "import secrets; print(secrets.token_hex(32))"
  # ----------------------------------------------------------------------
  - secret_name: "URL_SAFE_TOKEN_SECRET"
    sources:
      - kind: "env_var"
        env_var_name: "SOLIPLEX_URL_SAFE_TOKEN_SECRET"
      - kind: "random_chars"

  # ----------------------------------------------------------------------
  # Session middleware token
  #
  # If not set in the environment, we generate a random value at startup,
  # which will invalidate any existing sessions.
  #
  # To generate:
  #
  # $ python3 -c "import secrets; print(secrets.token_hex(32))"
  # ----------------------------------------------------------------------
  - secret_name: "SESSION_MIDDLEWARE_TOKEN"
    sources:
      - kind: "env_var"
        env_var_name: "SOLIPLEX_SESSION_MIDDLEWARE_TOKEN"
      - kind: "random_chars"

#==========================================================================
# Environment Variables
#==========================================================================
# See: https://soliplex.github.io/soliplex/config/environment/
#==========================================================================

environment:
  - "OLLAMA_BASE_URL"

  #------------------------------------------------------------------------
  # 'soliplex': on-disk configuration locations
  #------------------------------------------------------------------------

  - name: "INSTALLATION_PATH"
    value: "file:."

  - name: "RAG_LANCE_DB_PATH"
    value: "file:/db"

  #------------------------------------------------------------------------
  # Expire MCP room server tokens after one hour.
  #------------------------------------------------------------------------
  - name: "MCP_TOKEN_MAX_AGE"
    value: 3600

#==========================================================================
# Global 'haiku-rag' configuration
#==========================================================================
# See: https://soliplex.github.io/soliplex/config/rag/#global-configuration
#==========================================================================

haiku_rag_config_file: "./haiku.rag.yaml"

#==========================================================================
# FastAPI routers (custom)
#==========================================================================
# Add this project's own router (defined in src/${package_name}/views.py)
# by dotted name, without clearing the default Soliplex routers.
#==========================================================================
app_router_operations:
  - kind: "add"
    group_name: "${package_name}"
    router_name: "${package_name}.views.router"
    prefix: "/api"


#==========================================================================
# Agent configurations
#==========================================================================
# See: https://soliplex.github.io/soliplex/config/agents/
#==========================================================================

agent_configs:
  - id: "default_chat"
    model_name: "${chat_model}"
    system_prompt: |
      You are an expert AI assistant specializing in information retrieval.

      Your answers should be clear, concise, and ready for production use.

      Always provide code or examples in Markdown blocks.

  - id: "alternate_chat"
    model_name: "${chat_model_alt}"
    system_prompt: |
      You are an expert AI assistant specializing in information retrieval.

      Your answers should be clear, concise, and ready for production use.

      Always provide code or examples in Markdown blocks.

  - id: "title"
    model_name: "${title_model}"

title_agent_config_id: "title"

#==========================================================================
# Upload directory configuration
#==========================================================================
upload_path: /uploads

#==========================================================================
# Sandbox configuration
#==========================================================================
sandbox_config:
    environments_path: /sandbox/environments
    workdirs_path: /sandbox/workdirs

#==========================================================================
# Thread peristence:  SQLAlchemy DBURIs
#==========================================================================
thread_persistence_dburi:
  #sync: "sqlite://"
  #async: "sqlite+aiosqlite://"
  sync: "postgresql+psycopg://${agui_db}:secret:AGUI_DB_PASSWORD@postgres/${agui_db}"
  async: "postgresql+asyncpg://${agui_db}:secret:AGUI_DB_PASSWORD@postgres/${agui_db}"

#==========================================================================
# Authorization policy:  SQLAlchemy DBURIs
#==========================================================================
authorization_dburi:
  #sync: "sqlite://"
  #async: "sqlite+aiosqlite://"
  sync: "postgresql+psycopg://${authz_db}:secret:AUTHZ_DB_PASSWORD@postgres/${authz_db}"
  async: "postgresql+asyncpg://${authz_db}:secret:AUTHZ_DB_PASSWORD@postgres/${authz_db}"

#==========================================================================
# OIDC authentication provider configuration
#==========================================================================
# See: https://soliplex.github.io/soliplex/config/oidc_providers/
#==========================================================================

#==========================================================================
# Skills configuration
#==========================================================================
# See: https://soliplex.github.io/soliplex/config/skills/
#
# Default is equivalent to::
#
# filesystem_skills_paths:
#   - "./skills"
#
# ... but with no discovered skills enabled.
#==========================================================================

filesystem_skills_paths:
  - "./skills"

skill_configs:
  - skill_name: "bare-bones"
    kind: "filesystem"

# Requires installing 'haiku-skills-image-generation'
# - skill_name: "image-generation"
#   kind: "entrypoint"

#==========================================================================
# Rooms configuration
#==========================================================================
# See: https://soliplex.github.io/soliplex/config/rooms/
#
# A directory entry loads every room beneath it (each
# '<dir>/room_config.yaml'), so './rooms' loads all the rooms shipped here.
# To exclude a room -- e.g. one that needs extra secrets (an external model
# provider, or an MCP registry such as SmitheryAI) -- list the wanted rooms
# explicitly here instead of './rooms'.
#==========================================================================
room_paths:
  - "./rooms"

#==========================================================================
# Completions configuration
#==========================================================================
# See: https://soliplex.github.io/soliplex/config/completions/
#==========================================================================

#==========================================================================
# Quizzes configuration
#==========================================================================
# See: https://soliplex.github.io/soliplex/config/quizzes/
#==========================================================================

#==========================================================================
# Python logging configuration
#==========================================================================
# See: https://soliplex.github.io/soliplex/config/logging/
#==========================================================================
# logging_config_file: "./logging.yaml"

# logging_headers_map:
#   request_id: "X-Request-ID"

# logging_claims_map:
#   user_id: "email"

#==========================================================================
# Logfire configuration
#==========================================================================
# See: https://soliplex.github.io/soliplex/config/logfire/
#==========================================================================
