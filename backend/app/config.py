from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://studio:studio@127.0.0.1:5432/studio_ai"
    redis_url: str = "redis://127.0.0.1:6379/0"
    jwt_secret: str = "studio-ai-dev-secret-change-me"
    jwt_expire_hours: int = 72
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8080,http://localhost:8080"

    s3_endpoint: str = "http://127.0.0.1:9000"
    s3_public_endpoint: str = "http://127.0.0.1:9000"
    s3_access_key: str = "studioai"
    s3_secret_key: str = "studioai_secret"
    s3_bucket: str = "studio-audio"
    s3_region: str = "us-east-1"

    music_cost_credits: int = 10
    free_trial_credits: int = 200
    plan_monthly_credits: int = 200
    data_dir: str = "/data"

    bootstrap_admin_email: str = "admin@studio.ai"
    bootstrap_admin_password: str = "studio123"
    bootstrap_admin_name: str = "林制作人"

    dashscope_api_key: str = ""
    dashscope_workspace_id: str = ""
    dashscope_model: str = "qwen-plus"
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    music_provider: str = "runninghub"
    music_model: str = "minimax-music-2.5"
    runninghub_api_key: str = ""
    runninghub_base_url: str = "https://www.runninghub.cn"
    # Suno 等模型已要求走国际 AI 节点；MiniMax/Mureka 仍可用 CN。
    runninghub_ai_base_url: str = "https://www.runninghub.ai"
    audio_worker_url: str = "http://audio-worker:8090"
    suno_credit_cost: int = 20
    mureka_credit_cost: int = 50
    extend_credit_cost: int = 30

    @property
    def cors_origin_list(self):
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


settings = Settings()
