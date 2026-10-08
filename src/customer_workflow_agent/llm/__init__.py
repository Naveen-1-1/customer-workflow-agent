import os

# Set before LiteLLM is imported:
# - In its default "DEV" mode LiteLLM loads `.env` into the process environment on import,
#   which would hand our secrets to every subprocess and bypass Settings. We read .env ourselves.
# - It also downloads its model price list from GitHub unless told to use its bundled copy.
#   We don't use prices, and the app shouldn't need the network to start.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
