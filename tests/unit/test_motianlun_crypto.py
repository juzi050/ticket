from app.platforms.motianlun_crypto import decrypt_profile_value, encrypt_profile_value


def test_motianlun_profile_crypto_round_trip() -> None:
    encrypted = encrypt_profile_value("测试购票人", "public-service-key")

    assert encrypted != "测试购票人"
    assert decrypt_profile_value(encrypted, "public-service-key") == "测试购票人"
