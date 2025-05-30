from openai import AsyncOpenAI

async def generate_image(prompt, openai_api_key):
    client = AsyncOpenAI(api_key=openai_api_key)
    response = await client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        n=1,
        size="1024x1024"
    )
    return response.data[0].url
