from services.llm_client import ErrorText
from services.llm_types import LLMError
import pytest


def test_context_limit_failure_explains_cloud_request_was_not_sent():
    text = ErrorText(LLMError('all_candidates_failed', '所选模型不可用：context_limit',
                             failure_codes=['context_limit']))
    assert '上下文' in text
    assert '未发送' in text
    assert '管理' in text
    assert text.code == 'all_candidates_failed'


def test_transport_failure_explains_waiting_ended_without_confirming_billing():
    text = ErrorText(LLMError('all_candidates_failed', failure_codes=['provider_transport_error']))
    assert '连接失败或等待响应超时' in text
    assert '用量' in text


@pytest.mark.parametrize('code', ['budget_exhausted', 'budget_unconfigured'])
def test_budget_failure_explains_global_and_personal_limits_before_sending(code):
    for error in (LLMError(code), LLMError('all_candidates_failed', failure_codes=[code])):
        text = ErrorText(error)
        assert '全局' in text and '个人' in text
        assert '未发送' in text
        assert '日' in text and '月' in text
        assert '管理' in text
