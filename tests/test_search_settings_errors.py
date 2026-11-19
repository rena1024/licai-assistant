from tests.test_search_quota import config


def test_search_activation_reports_missing_verification_without_echoing_key(authenticated_client):
    values = config()
    values.update(evidence='', exclusive_key=False, storage_allowed=False, expected_version=0)
    response = authenticated_client.put('/api/search/channels', json=values)
    assert response.status_code == 422
    detail = response.json()['detail']
    assert '核验依据' in detail
    assert '凭证仅本应用使用' in detail
    assert '保存问答引用' in detail
    assert values['key'] not in response.text


def test_search_activation_keeps_saved_key_and_reports_version_conflict(authenticated_client):
    values = config()
    values.update(enabled=False, evidence='', expected_version=0)
    assert authenticated_client.put('/api/search/channels', json=values).status_code == 200
    values.update(enabled=True, evidence='已人工确认免费条件与引用存储条款', key='', expected_version=1)
    assert authenticated_client.put('/api/search/channels', json=values).status_code == 200
    channel = authenticated_client.get('/api/search/channels').json()['channels'][0]
    assert channel['enabled'] and channel['configured']
    assert channel['key'] == ''
    response = authenticated_client.put('/api/search/channels', json=values)
    assert response.status_code == 409
    assert '刷新' in response.json()['detail']


def test_search_invalid_payload_never_echoes_credentials(authenticated_client):
    values = config()
    values.update(remaining='synthetic-sensitive-value', expected_version=0)
    response = authenticated_client.put('/api/search/channels', json=values)
    assert response.status_code == 422
    assert 'synthetic-sensitive-value' not in response.text
    assert values['key'] not in response.text
