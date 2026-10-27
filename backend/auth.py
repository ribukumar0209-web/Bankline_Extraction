import ssl
import certifi

# This bypasses the corrupted Windows Certificate Store by using certifi directly
custom_context = ssl.create_default_context(cafile=certifi.where())
ssl._create_default_https_context = lambda: custom_context

import ee
print("Starting Earth Engine Authentication...")
ee.Authenticate(auth_mode='notebook')
print("Authentication successful! You can now start main.py")
