from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from app.auth.clerk_verify import get_auth
from app.core.config import settings
from app.services import stripe_svc, clerk_svc
import stripe, httpx, os

router = APIRouter(prefix="/billing", tags=["billing"])

class CheckoutPayload(BaseModel):
    price_id: str
    is_org: bool = False
    quantity: int | None = None

async def resolve_customer_id(is_org: bool, user_id: str, org_id: str|None):
    # Lee metadata desde Clerk
    async with httpx.AsyncClient() as c:
        if is_org:
            r = await c.get(f"https://api.clerk.com/v1/organizations/{org_id}", headers={"Authorization": f"Bearer {os.getenv('CLERK_SECRET_KEY')}"})
            r.raise_for_status()
            data = r.json()
            return data.get("private_metadata", {}).get("billing", {}).get("stripeCustomerId")
        else:
            r = await c.get(f"https://api.clerk.com/v1/users/{user_id}", headers={"Authorization": f"Bearer {os.getenv('CLERK_SECRET_KEY')}"})
            r.raise_for_status()
            data = r.json()
            return data.get("private_metadata", {}).get("billing", {}).get("stripeCustomerId")

@router.post("/checkout")
async def checkout(body: CheckoutPayload, req: Request):
    auth = await get_auth(req, jwks_url=settings.CLERK_JWKS_URL)
    customer_id = await resolve_customer_id(body.is_org, auth["user_id"], auth["org_id"])
    if not customer_id:
        # crea customer en Stripe
        customer = stripe.Customer.create(
            metadata={"clerk_user_id": auth["user_id"], "clerk_org_id": auth["org_id"] or ""},
        )
        customer_id = customer["id"]
        if body.is_org and auth["org_id"]:
            await clerk_svc.update_org_metadata(auth["org_id"], private={"billing": {"stripeCustomerId": customer_id}})
        else:
            await clerk_svc.update_user_metadata(auth["user_id"], private={"billing": {"stripeCustomerId": customer_id}})

    session = stripe_svc.create_checkout_session(
        customer_id=customer_id,
        price_id=body.price_id,
        quantity=body.quantity or 1,
        meta={"clerk_user_id": auth["user_id"], "clerk_org_id": auth["org_id"] or "", "plan_scope": "org" if body.is_org else "user"},
        success_url=f"{settings.FRONTEND_URL}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.FRONTEND_URL}/billing/cancel",
    )
    return {"checkout_url": session.url}

@router.post("/portal")
async def portal(req: Request):
    auth = await get_auth(req, jwks_url=settings.CLERK_JWKS_URL)
    # Para simplificar, tomamos siempre el customer del usuario (o de la org activa si hay org_id)
    is_org = bool(auth["org_id"])
    customer_id = await resolve_customer_id(is_org, auth["user_id"], auth["org_id"])
    if not customer_id:
        raise HTTPException(400, "Customer not found")
    portal = stripe_svc.create_billing_portal(customer_id, f"{settings.FRONTEND_URL}/settings/billing")
    return {"portal_url": portal.url}
