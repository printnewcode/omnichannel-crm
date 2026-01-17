import requests

url = 'http://localhost:8000/api/messages/18/download_media/'
headers = {
    'Authorization': 'Token a608e0d7d827e97655056e3871eabbbc905e6ded',
    'Accept': 'application/json'
}

print("Testing media download API with proper headers...")

try:
    response = requests.get(url, headers=headers, allow_redirects=False)
    print(f'Status: {response.status_code}')
    print(f'Headers: {dict(response.headers)}')

    if response.status_code == 302:
        print("SUCCESS: Redirect received")
        location = response.headers.get('Location')
        print(f'Redirect location: {location}')

        # Попробуем перейти по редиректу
        if location:
            redirect_response = requests.get(f'http://localhost:8000{location}', allow_redirects=False)
            print(f'Redirect response status: {redirect_response.status_code}')
            print(f'Redirect content-type: {redirect_response.headers.get("content-type")}')
    else:
        print(f'ERROR: Unexpected status code')
        print(f'Response: {response.text[:200]}')

except Exception as e:
    print(f'ERROR: {e}')
    import traceback
    traceback.print_exc()