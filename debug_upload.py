from website.dashboard import app
import io, json

client = app.test_client()
data = {'executor_headed': 'no', 'cases_file': (io.BytesIO(b'{"id":"C1"}'), 'cases.json')}
resp = client.post('/api/automation-jobs', data=data, content_type='multipart/form-data')
print('status', resp.status_code)
print(resp.get_data(as_text=True))
print('files', resp.request)
