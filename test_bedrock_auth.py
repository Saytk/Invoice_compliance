import os
import sys
import boto3
import botocore

def log(title, value):
    print(f"[DEBUG] {title}: {value}")

print("========== AWS DEBUG ==========")

# --- Environnement ---
log("PYTHON_EXECUTABLE", sys.executable)
log("AWS_PROFILE", os.environ.get("AWS_PROFILE"))
log("AWS_DEFAULT_REGION", os.environ.get("AWS_DEFAULT_REGION"))
log("AWS_ACCESS_KEY_ID", "SET" if os.environ.get("AWS_ACCESS_KEY_ID") else "NOT SET")
log("AWS_SECRET_ACCESS_KEY", "SET" if os.environ.get("AWS_SECRET_ACCESS_KEY") else "NOT SET")

# --- Chemins AWS attendus ---
home = os.path.expanduser("~")
log("HOME", home)
log("AWS_CREDENTIALS_PATH", os.path.join(home, ".aws", "credentials"))
log("AWS_CONFIG_PATH", os.path.join(home, ".aws", "config"))

print("\n========== FILE CHECK ==========")
print("credentials exists:",
      os.path.exists(os.path.join(home, ".aws", "credentials")))
print("config exists:",
      os.path.exists(os.path.join(home, ".aws", "config")))

print("\n========== BOTO3 SESSION ==========")

try:
    session = boto3.Session()
    log("session.profile_name", session.profile_name)
    creds = session.get_credentials()

    if creds is None:
        log("credentials", "NONE")
    else:
        frozen = creds.get_frozen_credentials()
        log("access_key", frozen.access_key)
        log("secret_key", "SET" if frozen.secret_key else "NOT SET")
        log("token", "SET" if frozen.token else "NOT SET")

except Exception as e:
    print("[ERROR] Session init failed:", repr(e))

print("\n========== STS CALL ==========")

try:
    sts = boto3.client("sts")
    identity = sts.get_caller_identity()
    print("[SUCCESS] get_caller_identity:")
    print(identity)

except botocore.exceptions.NoCredentialsError:
    print("[ERROR] NoCredentialsError: boto3 found NO credentials")

except Exception as e:
    print("[ERROR] STS call failed:", repr(e))

print("========== END DEBUG ==========")
