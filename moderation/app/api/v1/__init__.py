from fastapi import APIRouter
import app.api.v1.auth as auth
import app.api.v1.moderators as moderators
import app.api.v1.b2b_events as b2b_events
import app.api.v1.tickets as tickets

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(moderators.router, prefix="/moderators", tags=["Moderators"])
api_router.include_router(b2b_events.router, prefix="/b2b/events", tags=["B2B Events"])
api_router.include_router(tickets.router, prefix="/tickets", tags=["Tickets"])
