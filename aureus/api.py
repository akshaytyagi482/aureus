"""AureusRecon HTTP API and web app host."""

from contextlib import asynccontextmanager
from datetime import date
from typing import Literal
from urllib.parse import quote_plus

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from . import accounts, config, crypto, db, gstin as gstin_mod, integrations, mailer, sample, services
from .config import BASE_DIR
from .engines import policy
from .engines.deadlines import period_label
from .ingestion import gstr2b, ims, parse_books, parse_gstr2b, parse_ims, zoho
from .outputs import forms, notice

MAX_UPLOAD_BYTES = 20 * 1024 * 1024



@asynccontextmanager
async def lifespan(_app):
    if config.ENV == "production":
        crypto.check_key()  # fail the deploy now, not on the first import
    db.init()
    yield
    db.close()


app = FastAPI(title="AureusRecon", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json",
              lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if not request.url.path.startswith("/api/docs"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(services.AccessDenied)
def _denied(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=403)


@app.exception_handler(services.ConsentRequired)
def _consent(_, exc):
    return JSONResponse({"detail": str(exc), "code": "consent_required"}, status_code=403)


@app.exception_handler(integrations.NotConfigured)
def _not_configured(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=501)


@app.exception_handler(ValueError)
def _bad_value(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ---------------------------------------------------------------- auth

def current_user(authorization: str = Header(default="")) -> dict:
    scheme, _, token = authorization.partition(" ")
    user = services.user_for_token(token) if scheme.lower() == "bearer" and token else None
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


class SignupBody(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=200)
    role: Literal["msme", "ca"]
    firm_name: str | None = Field(default=None, max_length=160)


class LoginBody(BaseModel):
    email: EmailStr
    password: str


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.exception_handler(accounts.TooManyAttempts)
def _too_many(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=429, headers={"Retry-After": str(exc.minutes * 60)})


@app.exception_handler(mailer.MailNotConfigured)
def _no_mail(_, exc):
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.post("/api/auth/signup")
def signup(body: SignupBody, request: Request):
    accounts.check_throttle("signup-ip", _ip(request), 10)
    accounts.record_failure("signup-ip", _ip(request))  # counts signups per IP
    try:
        services.create_user(body.email, body.name, body.password, body.role, body.firm_name)
    except services.EmailTaken:
        raise HTTPException(409, "An account with this email already exists")
    token, user = services.login(body.email, body.password)
    return {"token": token, "user": user}


@app.post("/api/auth/login")
def login(body: LoginBody, request: Request):
    result = accounts.guarded_login(services.login, body.email, body.password, _ip(request))
    if not result:
        raise HTTPException(401, "Email or password is incorrect")
    token, user = result
    return {"token": token, "user": user}


@app.post("/api/auth/logout")
def logout(authorization: str = Header(default="")):
    services.logout(authorization.partition(" ")[2])
    return {"ok": True}


class ResetRequestBody(BaseModel):
    email: EmailStr


class ResetConfirmBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


@app.post("/api/auth/password-reset/request")
def password_reset_request(body: ResetRequestBody, request: Request):
    accounts.request_reset(body.email, _ip(request))
    return {"ok": True, "detail": "If an account uses this email, a reset link is on its way."}


@app.post("/api/auth/password-reset/confirm")
def password_reset_confirm(body: ResetConfirmBody):
    accounts.confirm_reset(body.token, body.password)
    return {"ok": True}


@app.post("/api/auth/password")
def change_password(body: ChangePasswordBody, user=Depends(current_user)):
    accounts.change_password(user["id"], body.current_password, body.new_password)
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return user


# ---------------------------------------------------------------- organisations

Zone = Literal["A", "B", "C", "D", "D+", "VM", "NI"]


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    gstin: str
    district: str | None = None
    address: str | None = None
    sector: str = "manufacturing"
    industry: str | None = None
    udyam: str | None = None
    annual_turnover: float | None = Field(default=None, ge=0)
    investment_pm: float | None = Field(default=None, ge=0)
    term_loan_amount: float | None = Field(default=None, ge=0)
    zone: Zone | None = None
    monthly_electricity_duty: float | None = Field(default=None, ge=0)
    monthly_power_units: float | None = Field(default=None, ge=0)
    filing_frequency: Literal["monthly", "quarterly"] = "monthly"


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    district: str | None = None
    address: str | None = None
    sector: str | None = None
    industry: str | None = None
    udyam: str | None = None
    annual_turnover: float | None = Field(default=None, ge=0)
    investment_pm: float | None = Field(default=None, ge=0)
    term_loan_amount: float | None = Field(default=None, ge=0)
    zone: Zone | None = None
    monthly_electricity_duty: float | None = Field(default=None, ge=0)
    monthly_power_units: float | None = Field(default=None, ge=0)
    filing_frequency: Literal["monthly", "quarterly"] | None = None


@app.get("/api/orgs")
def list_orgs(user=Depends(current_user)):
    return services.list_orgs(user)


@app.post("/api/orgs")
def create_org(body: OrgCreate, user=Depends(current_user)):
    return services.create_org(user, body.model_dump())


@app.get("/api/orgs/{org_id}")
def get_org(org_id: int, user=Depends(current_user)):
    org = services.get_org(user, org_id)
    org["consents"] = services.active_consents(org_id)
    org["datasets"] = services.list_datasets(user, org_id)
    org["members"] = services.list_members(user, org_id)
    org["zoho"] = integrations.status(org_id)
    return org


@app.patch("/api/orgs/{org_id}")
def update_org(org_id: int, body: OrgUpdate, user=Depends(current_user)):
    return services.update_org(user, org_id, body.model_dump(exclude_unset=True))


@app.delete("/api/orgs/{org_id}")
def delete_org(org_id: int, user=Depends(current_user)):
    services.delete_org(user, org_id)
    return {"ok": True}


class MemberBody(BaseModel):
    email: EmailStr


@app.post("/api/orgs/{org_id}/members")
def add_member(org_id: int, body: MemberBody, user=Depends(current_user)):
    return services.add_member(user, org_id, body.email)


@app.get("/api/orgs/{org_id}/audit")
def audit_trail(org_id: int, user=Depends(current_user)):
    return services.audit_trail(user, org_id)


# ---------------------------------------------------------------- consent

class ConsentBody(BaseModel):
    purpose: str


@app.post("/api/orgs/{org_id}/consent")
def grant_consent(org_id: int, body: ConsentBody, user=Depends(current_user)):
    return services.grant_consent(user, org_id, body.purpose)


@app.post("/api/orgs/{org_id}/consent/revoke")
def revoke_consent(org_id: int, body: ConsentBody, user=Depends(current_user)):
    return services.revoke_consent(user, org_id, body.purpose)


# ---------------------------------------------------------------- ingestion

async def _read(upload: UploadFile) -> bytes:
    payload = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is larger than 20 MB")
    return payload


@app.post("/api/orgs/{org_id}/import/books")
async def import_books(org_id: int, source: str = Form(...), file: UploadFile = File(...), user=Depends(current_user)):
    services.require_member(user, org_id)
    invoices = parse_books(source, await _read(file))
    return services.store_dataset(org_id, user["id"], "books", source, invoices)


def _check_buyer(org: dict, payload: bytes, kind: str = "gstr2b"):
    reader, label = (ims.buyer_gstin, "IMS file") if kind == "ims" else (gstr2b.buyer_gstin, "GSTR-2B")
    try:
        owner = reader(payload)
    except Exception:
        owner = ""
    if owner and owner != org["gstin"]:
        raise HTTPException(400, f"This {label} belongs to GSTIN {owner}, not {org['name']} ({org['gstin']})")


@app.post("/api/orgs/{org_id}/import/ims")
async def import_ims(org_id: int, file: UploadFile = File(...), user=Depends(current_user)):
    org = services.get_org(user, org_id)
    payload = await _read(file)
    records = parse_ims(payload)
    _check_buyer(org, payload, "ims")
    return services.store_dataset(org_id, user["id"], "ims", "ims", records)


@app.post("/api/orgs/{org_id}/import/gstr2b")
async def import_gstr2b(org_id: int, file: UploadFile = File(...), user=Depends(current_user)):
    org = services.get_org(user, org_id)
    payload = await _read(file)
    invoices = parse_gstr2b(payload)
    _check_buyer(org, payload)
    return services.store_dataset(org_id, user["id"], "gstr2b", "gstr2b", invoices)


@app.post("/api/orgs/{org_id}/import/sample")
def import_sample(org_id: int, user=Depends(current_user)):
    services.require_member(user, org_id)
    books = services.store_dataset(org_id, user["id"], "books", "tally", parse_books("tally", sample.tally_xml()))
    portal = services.store_dataset(org_id, user["id"], "gstr2b", "gstr2b", parse_gstr2b(sample.gstr2b_json()))
    ims_data = services.store_dataset(org_id, user["id"], "ims", "ims", parse_ims(sample.ims_json()))
    return {"books": books, "gstr2b": portal, "ims": ims_data}


@app.delete("/api/orgs/{org_id}/datasets/{dataset_id}")
def delete_dataset(org_id: int, dataset_id: int, user=Depends(current_user)):
    services.delete_dataset(user, org_id, dataset_id)
    return {"ok": True}


@app.post("/api/orgs/{org_id}/connector-token")
def connector_token(org_id: int, user=Depends(current_user)):
    return {"token": services.create_connector_token(user, org_id),
            "note": "Shown once. Use it with connector/tally_push.py on the machine running Tally Prime."}


@app.post("/api/connector/import/{kind}")
async def connector_import(kind: Literal["books", "gstr2b", "ims"], source: str = Form("tally"), file: UploadFile = File(...),
                           x_connector_token: str = Header(default="")):
    org_id = services.org_for_connector_token(x_connector_token) if x_connector_token else None
    if not org_id:
        raise HTTPException(401, "Invalid connector token")
    payload = await _read(file)
    if kind == "books":
        invoices = parse_books(source, payload)
    else:
        invoices = parse_ims(payload) if kind == "ims" else parse_gstr2b(payload)
        _check_buyer(services.get_org_by_id(org_id), payload, kind)
    return services.store_dataset(org_id, None, kind, source if kind == "books" else kind, invoices)


# ---------------------------------------------------------------- Zoho Books OAuth

@app.get("/api/orgs/{org_id}/integrations/zoho/connect")
def zoho_connect(org_id: int, user=Depends(current_user)):
    services.require_member(user, org_id)
    return {"authorize_url": integrations.authorize_url(org_id, user["id"])}


@app.get("/api/integrations/zoho/callback")
def zoho_callback(code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse("/#/error?message=Zoho+connection+was+cancelled")
    try:
        org_id = integrations.complete(code, state)
    except PermissionError as exc:
        return RedirectResponse(f"/#/error?message={quote_plus(str(exc))}")
    return RedirectResponse(f"/#/org/{org_id}/data?zoho=connected")


class ZohoSyncBody(BaseModel):
    date_from: date
    date_to: date


@app.post("/api/orgs/{org_id}/integrations/zoho/sync")
def zoho_sync(org_id: int, body: ZohoSyncBody, user=Depends(current_user)):
    services.require_member(user, org_id)
    try:
        payload = integrations.fetch_bills(org_id, body.date_from.isoformat(), body.date_to.isoformat())
    except PermissionError as exc:
        raise HTTPException(409, str(exc))
    stored = services.replace_by_period(org_id, user["id"], "books", "zoho", zoho.parse_data(payload))
    if not stored:
        raise HTTPException(404, "Zoho Books returned no bills for these dates")
    return {"records": sum(d["records"] for d in stored), "periods": [p for d in stored for p in d["periods"]],
            "kind": "books", "source": "zoho"}


# ---------------------------------------------------------------- tax engine

@app.get("/api/orgs/{org_id}/reconciliation")
def reconciliation(org_id: int, period: str | None = Query(default=None, pattern=r"^\d{6}$"), user=Depends(current_user)):
    org = services.get_org(user, org_id)
    return services.run_reconciliation(org, period)


@app.get("/api/orgs/{org_id}/suppliers/{supplier_gstin}/reminder")
def supplier_reminder(org_id: int, supplier_gstin: str, period: str | None = None, user=Depends(current_user)):
    org = services.get_org(user, org_id)
    recon = services.run_reconciliation(org, period)
    supplier = next((s for s in recon["summary"]["suppliers_at_risk"] if s["supplier_gstin"] == supplier_gstin.upper()), None)
    if not supplier:
        raise HTTPException(404, "Nothing is pending with this supplier")
    due = date.fromisoformat(recon["filing"]["gstr3b_due_on"]).strftime("%d %b %Y")
    return notice.supplier_reminder(org, supplier, recon["results"], due)


class DefenseBody(BaseModel):
    notice_type: Literal["drc01c", "asmt10", "drc01"]
    result_ids: list[str] | None = None
    notice_ref: str = Field(default="", max_length=80)
    notice_date: str = Field(default="", max_length=20)
    period: str | None = Field(default=None, pattern=r"^\d{6}$")
    polish: bool = False


@app.post("/api/orgs/{org_id}/notice-copilot")
def notice_copilot(org_id: int, body: DefenseBody, user=Depends(current_user)):
    org = services.get_org(user, org_id)
    recon = services.run_reconciliation(org, body.period)
    rows = recon["results"]
    if body.result_ids:
        wanted = set(body.result_ids)
        rows = [r for r in rows if r["id"] in wanted]
    rows = [r for r in rows if r["status"] != "MATCHED_ELIGIBLE"]
    if not rows:
        raise HTTPException(400, "No mismatched or missing invoices selected")
    draft = notice.draft_reply(org, rows, body.notice_type, body.notice_ref, body.notice_date,
                               period_label(body.period) if body.period else "")
    return notice.polish_with_claude(draft) if body.polish else draft


# ---------------------------------------------------------------- IMS

@app.get("/api/orgs/{org_id}/ims")
def ims_recommendations(org_id: int, period: str | None = Query(default=None, pattern=r"^\d{6}$"), user=Depends(current_user)):
    return services.ims_recommendations(services.get_org(user, org_id), period)


class ImsUploadBody(BaseModel):
    ids: list[str] = Field(min_length=1)
    overrides: dict[str, Literal["A", "R", "P", "N"]] = {}
    period: str | None = Field(default=None, pattern=r"^\d{6}$")


@app.post("/api/orgs/{org_id}/ims/upload-file")
def ims_upload_file(org_id: int, body: ImsUploadBody, user=Depends(current_user)):
    org = services.get_org(user, org_id)
    data = services.ims_upload(org, body.ids, body.overrides, body.period)
    services.audit(org_id, user["id"], "ims_file_generated", {"documents": sum(len(v) for v in data["invdata"].values())})
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="ims-actions-{org["gstin"]}.json"'})


# ---------------------------------------------------------------- policy engine

@app.get("/api/orgs/{org_id}/subsidies")
def subsidies(org_id: int, user=Depends(current_user)):
    return services.run_policy(services.get_org(user, org_id))


def _scheme(org_id: int, scheme_id: str, user):
    org = services.get_org(user, org_id)
    result = services.run_policy(org)
    scheme = next((s for s in result["schemes"] if s["scheme_id"] == scheme_id), None)
    if not scheme:
        raise HTTPException(404, "This scheme does not apply to the organisation")
    return org, scheme


@app.get("/api/orgs/{org_id}/subsidies/{scheme_id}/application")
def application(org_id: int, scheme_id: str, user=Depends(current_user)):
    org, scheme = _scheme(org_id, scheme_id, user)
    return forms.build_packet(org, scheme)


@app.get("/api/orgs/{org_id}/subsidies/{scheme_id}/application.html", response_class=HTMLResponse)
def application_html(org_id: int, scheme_id: str, user=Depends(current_user)):
    org, scheme = _scheme(org_id, scheme_id, user)
    return HTMLResponse(forms.render_html(forms.build_packet(org, scheme)),
                        headers={"Content-Disposition": f'attachment; filename="{scheme_id}-application.html"'})


# ---------------------------------------------------------------- CA console

@app.get("/api/console")
def console(user=Depends(current_user)):
    clients = [services.org_summary(org) for org in services.list_orgs(user)]
    clients.sort(key=lambda c: -c["tax"]["itc_at_risk"])
    return {
        "firm_name": user.get("firm_name"),
        "clients": clients,
        "totals": {
            "clients": len(clients),
            "itc_at_risk": round(sum(c["tax"]["itc_at_risk"] for c in clients), 2),
            "subsidy_potential": round(sum(c["subsidy"]["total_estimated_benefit"] for c in clients), 2),
            "awaiting_data": sum(1 for c in clients if not c["has_data"]),
        },
    }


# ---------------------------------------------------------------- public

class CalculatorBody(BaseModel):
    state_code: str = Field(pattern=r"^\d{2}$")
    sector: str = "manufacturing"
    category: Literal["micro", "small", "medium"] | None = None
    annual_turnover: float | None = Field(default=None, ge=0)
    investment_pm: float | None = Field(default=None, ge=0)
    investments: dict[str, float] = {}
    term_loan_amount: float | None = Field(default=None, ge=0)
    zone: Zone | None = None
    monthly_electricity_duty: float | None = Field(default=None, ge=0)
    monthly_power_units: float | None = Field(default=None, ge=0)


@app.post("/api/public/subsidy-calculator")
def calculator(body: CalculatorBody):
    return policy.calculator(body.state_code, body.sector, body.investments, body.category, body.annual_turnover,
                             body.investment_pm, body.term_loan_amount, body.zone, body.monthly_electricity_duty,
                             body.monthly_power_units)


@app.get("/api/public/meta")
def meta():
    grid = policy.rule_grid()
    return {
        "states": [{"code": c, "name": n} for c, n in sorted(gstin_mod.STATES.items(), key=lambda kv: kv[1])],
        "asset_classes": [{"id": k, "label": v["label"]} for k, v in grid["asset_classes"].items()],
        "zones_by_state": {code: {"zones": [{"id": k, "label": v} for k, v in z["zones"].items()],
                                  "lookup": z.get("lookup"), "source": z.get("source"), "source_url": z.get("source_url")}
                           for code, z in grid.get("zones_by_state", {}).items()},
        "schemes": [{"id": s["id"], "name": s["name"], "states": s["states"], "verified": s["verified"]} for s in grid["schemes"]],
        "coverage": grid.get("coverage", {}),
        "notice_types": [{"id": k, "title": v["title"]} for k, v in notice.NOTICE_TYPES.items()],
        "purposes": [{"id": k, **v} for k, v in services.PURPOSES.items()],
    }


@app.get("/api/public/gstin/{value}")
def check_gstin(value: str):
    return gstin_mod.describe(value)


@app.get("/api/public/sample/{name}")
def sample_file(name: Literal["tally.xml", "gstr2b.json", "ims.json"]):
    if name == "ims.json":
        return HTMLResponse(sample.ims_json(), media_type="application/json",
                            headers={"Content-Disposition": 'attachment; filename="sample-ims.json"'})
    if name == "tally.xml":
        return HTMLResponse(sample.tally_xml(), media_type="application/xml",
                            headers={"Content-Disposition": 'attachment; filename="sample-tally-purchases.xml"'})
    return HTMLResponse(sample.gstr2b_json(), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="sample-gstr2b-092026.json"'})


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=BASE_DIR / "web", html=True), name="web")
