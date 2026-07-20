from app.domain import BuyerProfile
from app.gui.buyer_panel import build_buyer_profile


def test_build_buyer_profile_preserves_generated_identity_when_editing() -> None:
    original = BuyerProfile(
        name="测试甲",
        certificate_type="身份证",
        certificate_number="110101199001011234",
        phone="13800138000",
    )
    edited = build_buyer_profile(
        {
            "name": "测试乙",
            "certificate_type": "身份证",
            "certificate_number": "110101199001011234",
            "phone": "13900139000",
        },
        original,
    )
    assert edited.buyer_id == original.buyer_id
    assert edited.created_at == original.created_at
    assert edited.name == "测试乙"
