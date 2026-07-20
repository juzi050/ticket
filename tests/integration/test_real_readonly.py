import httpx

from app.platforms.piaoniu_api import PiaoniuApi
from app.storage.audit_repository import AuditRepository
from app.storage.database import MvpDatabase


PIAONIU_EVENT_URL = "https://www.piaoniu.com/activity/779707"


async def test_real_piaoniu_event(tmp_path) -> None:
    database = MvpDatabase(tmp_path / "ticket.db")
    await database.initialize()
    client = httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=20
    )
    api = PiaoniuApi(client, AuditRepository(database))
    try:
        event = await api.get_event(PIAONIU_EVENT_URL)
        sessions = await api.list_sessions(event.event_id)
        tickets = await api.list_tickets(event.event_id, sessions[0].session_id, 1)
        exact = await api.get_exact_ticket(tickets[0], 1)
    finally:
        await api.close()

    assert event.event_id == "779707"
    assert "洛天依" in event.event_name
    assert sessions
    assert all(session.event_id == event.event_id for session in sessions)
    assert tickets
    assert all(ticket.event_id == event.event_id for ticket in tickets)
    assert exact is not None and exact.listing_id == tickets[0].listing_id
