# 离线协议样例

本目录数据按官方公开协议构造，均为合成模型、请求 ID、工具和 Token；不是实际付费请求抓包。`test_llm_cloud_adapters.py` 直接读取这些文件，验证 Responses、DeepSeek 和通义千问的工具参数及用量口径；其他原生和流协议结构由对应测试覆盖。

官方来源、协议差异和未实测边界见 `docs/deployment/controlled-ai.md`。缓存为输入的子集、推理为输出的子集，不能无条件重复相加。真实型号／地域／账户验收必须另行记录。
