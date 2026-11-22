from fastapi import APIRouter

from app.api.v1 import admin, auth, chat, plans, products, profile, quotes

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(products.router)
api_router.include_router(admin.router)
api_router.include_router(chat.router)
api_router.include_router(quotes.router)
api_router.include_router(plans.router)
api_router.include_router(profile.router)
