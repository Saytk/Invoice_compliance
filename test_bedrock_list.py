import boto3

# Bedrock control plane (liste des modèles dispo dans ta région)
br = boto3.client("bedrock")

resp = br.list_foundation_models()
print("n_models:", len(resp.get("modelSummaries", [])))

# Affiche juste les modèles OpenAI (si présents)
for m in resp.get("modelSummaries", []):
    mid = m.get("modelId", "")
    prov = m.get("providerName", "")
    if "openai" in (prov or "").lower() or "openai" in (mid or "").lower():
        print(prov, mid)
