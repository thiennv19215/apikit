import asyncio
import json
import logging
from agent.services.flow_client import get_flow_client
from agent.services import flow_batch as fb

logging.basicConfig(level=logging.INFO)

async def main():
    client = get_flow_client()
    # Wait for extension connection
    for _ in range(10):
        if client.connected:
            break
        await asyncio.sleep(1)
    
    pid = client.active_project_id
    print(f"Connected: {client.connected}, Project: {pid}")

    # Use the real image media_id we generated earlier
    media_id = "4bfd4d2e-41bc-4b74-9588-c5240630d93d"
    prompt = "A cat moving slightly, cinematic"

    test_models = [
        "veo_3_1_i2v_lite_low_priority",
        "veo_3_1_i2v_lite",
        "veo_3_1_i2v_s_fast_ultra",
    ]

    for model in test_models:
        print(f"\n--- Testing model: {model} ---")
        try:
            freq = fb.video_request(prompt, pid, media_id, aspect=1, model=model)
            res = await client._batch_payload(
                fb.RPC_GEN_VIDEO, freq, fb.CAPTCHA_VIDEO, timeout=30
            )
            print("SUCCESS! Payload:", res)
        except Exception as e:
            print("FAILED:", type(e).__name__, str(e))

if __name__ == "__main__":
    asyncio.run(main())
