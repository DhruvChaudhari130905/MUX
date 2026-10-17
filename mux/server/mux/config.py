"""Settings loaded from environment variables (Token Factory, the sandbox, Tavily, Supabase, GitHub, Postgres)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database (empty database_url: mux.dbsession falls back to local SQLite)
    database_url: str = ""
    test_database_url: str = ""
    debug: bool = False

    # Token Factory
    token_factory_api_key: str = ""
    token_factory_base_url: str = ""
    model_lightning: str = ""
    model_super: str = ""
    model_ultra: str = ""

    # Sandbox for the coder's builds and tests: "docker" (this machine), "nebius" (Token Factory
    # Sandboxes, needs the key and URL below) or "none"
    sandbox_provider: str = "docker"
    sandbox_image: str = ""  # empty: mux-sandbox for Docker
    sandbox_api_key: str = ""
    sandbox_base_url: str = ""

    # Tavily
    tavily_api_key: str = ""

    # Supabase
    supabase_jwt_secret: str = ""
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_audience: str = ""
    supabase_jwt_issuer: str = ""
    # Allow unverified JWT tokens (local development only - NEVER enable in production)
    allow_unverified_tokens: bool = False

    # GitHub OAuth
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = ""
    github_token_encryption_key: str = ""

    # Where the web app runs; the GitHub connect flow returns the browser here
    web_app_url: str = "http://localhost:3000"

    # Room secrets (MCP server tokens, room AI keys) are encrypted with room_secrets_key (a Fernet key).
    # allow_private_urls lets rooms use http:// and private-network addresses: local development only.
    # The mcp_* names are the old ones, still read when the new ones are empty/false.
    room_secrets_key: str = ""
    allow_private_urls: bool = False
    mcp_config_path: str = "mcp.json"
    mcp_encryption_key: str = ""
    mcp_allow_private_urls: bool = False

    # Skill folders (each with a SKILL.md) the coder can use when a room switches them on (mux.skills)
    skills_path: str = "skills"


settings = Settings()
