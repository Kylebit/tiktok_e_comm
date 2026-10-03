def test_full_handler_finance_and_navigation(tmp_path,record_property,monkeypatch):
    from test_u05_finance_browser import test_u05_real_finance_browser
    monkeypatch.setenv('ORBIT_FULL_HANDLER','1')
    test_u05_real_finance_browser(tmp_path,record_property)
