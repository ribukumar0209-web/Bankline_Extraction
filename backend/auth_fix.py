import ssl
import certifi
import ee

def create_patched_context(*args, **kwargs):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.options |= ssl.OP_NO_SSLv2
    context.options |= ssl.OP_NO_SSLv3
    context.options |= ssl.OP_NO_TLSv1
    context.options |= ssl.OP_NO_TLSv1_1
    context.load_verify_locations(cafile=certifi.where())
    return context

# Monkeypatch ssl.create_default_context to bypass the broken Windows Certificate Store
ssl.create_default_context = create_patched_context

print("Running patched Earth Engine Authenticator...")
print("Please follow the instructions below:")
ee.Authenticate(auth_mode='notebook')
print("\nAuthentication complete! You can now run main.py")
