from dotenv import load_dotenv
load_dotenv()

import requests, os

resp = requests.get(
    os.getenv("LITELLM_URL") + "/v1/models",
    headers={"Authorization": "Bearer " + os.getenv("LITELLM_KEY")}
)
print(resp.json())
