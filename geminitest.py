from decouple import config
from google import genai

client = genai.Client(api_key=config('GEMINI_API_KEY'))
model = config('GEMINI_MODEL', default='gemini-3.5-flash')

print("Trying model:", model)
try:
    r = client.models.generate_content(model=model, contents="Say hi in 5 words")
    print("SUCCESS:", r.text)
except Exception as e:
    print("ERROR TYPE:", type(e).__name__)
    print("ERROR:", e)
    print("\nModels your key can use:")
    for m in client.models.list():
        print(" ", m.name)