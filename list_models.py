import os
try:
    from google import genai
except ImportError:
    print("Please pip install google-genai first.")
    exit()

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    print("GEMINI_API_KEY environment variable not found.")
    exit()

client = genai.Client()

print("Fetching available models for your API Key...\n")
try:
    models = client.models.list()
    print("Models supporting Image Generation:")
    print("-" * 50)
    found = False
    for m in models:
        # Check if the model name contains image-related keywords
        name = m.name.lower()
        if "image" in name or "imagen" in name or "vision" in name:
            print(f"Model ID: {m.name}")
            if hasattr(m, 'description'):
                print(f"Description: {m.description}")
            print("-" * 50)
            found = True
            
    if not found:
        print("No image generation models were found for this API Key.")
        print("This might mean your API key tier does not support image generation yet.")
        
except Exception as e:
    print(f"Error listing models: {e}")
