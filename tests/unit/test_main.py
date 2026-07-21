from unittest.mock import Mock

import main as entrypoint


def test_main_launches_current_mvp_application(monkeypatch) -> None:
    root = Mock()
    application = Mock()
    monkeypatch.setattr(entrypoint.tk, "Tk", Mock(return_value=root))
    monkeypatch.setattr(
        entrypoint, "MvpApplication", Mock(return_value=application)
    )

    entrypoint.main()

    entrypoint.MvpApplication.assert_called_once_with(root)
    root.mainloop.assert_called_once_with()
