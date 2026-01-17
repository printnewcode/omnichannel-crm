import requests

url = 'http://localhost:8000/api/messages/18/download_media/'
headers = {'Authorization': 'Token a608e0d7d827e97655056e3871eabbbc905e6ded'}

print("Testing media download API...")

try:
    response = requests.get(url, headers=headers, allow_redirects=False)
    print(f'Status: {response.status_code}')
    print(f'Location: {response.headers.get("Location", "None")}')

    if response.status_code == 302:
        print("SUCCESS: Redirect to media file received")
    else:
        print(f'ERROR: Unexpected status code')
        print(f'Response: {response.text[:200]}')

except Exception as e:
    print(f'ERROR: {e}')