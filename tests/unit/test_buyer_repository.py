import aiosqlite

from app.domain import BuyerPlatformBinding, BuyerProfile
from app.storage.buyer_repository import BuyerBindingRepository, BuyerRepository
from app.storage.database import MvpDatabase


async def test_buyer_crud_persists_full_local_business_data(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    repository = BuyerRepository(database)
    buyer = BuyerProfile(
        name="测试甲",
        certificate_type="身份证",
        certificate_number="110101199001011234",
        phone="13900001234",
    )

    saved = await repository.save(buyer)
    restored = await repository.get(saved.buyer_id)

    assert restored is not None
    assert restored.certificate_number == "110101199001011234"
    assert restored.phone == "13900001234"
    edited = await repository.save(restored.model_copy(update={"name": "测试乙"}))
    assert (await repository.get(edited.buyer_id)).name == "测试乙"  # type: ignore[union-attr]

    async with aiosqlite.connect(database.path) as connection:
        row = await (
            await connection.execute(
                "SELECT certificate_number, phone FROM buyers WHERE buyer_id=?",
                (saved.buyer_id,),
            )
        ).fetchone()
    assert row == ("110101199001011234", "13900001234")

    await repository.delete(saved.buyer_id)
    assert await repository.get(saved.buyer_id) is None


async def test_remote_binding_is_reused_and_cascades_on_delete(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    buyers = BuyerRepository(database)
    bindings = BuyerBindingRepository(database)
    buyer = await buyers.save(
        BuyerProfile(
            name="测试甲",
            certificate_type="身份证",
            certificate_number="110101199001011234",
        )
    )
    binding = BuyerPlatformBinding(
        buyer_id=buyer.buyer_id,
        platform="motianlun",
        remote_buyer_id="remote-1",
        remote_payload={"certificateType": "ID_CARD"},
    )

    await bindings.save(binding)

    restored = await bindings.get(buyer.buyer_id, "motianlun")
    assert restored is not None
    assert restored.remote_buyer_id == "remote-1"
    assert restored.remote_payload == {"certificateType": "ID_CARD"}

    await buyers.delete(buyer.buyer_id)
    assert await bindings.get(buyer.buyer_id, "motianlun") is None
