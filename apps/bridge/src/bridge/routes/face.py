"""The operator face gate over HTTP (bridge/face_gate.py): who is enrolled, the live
settings, enrollment, and whether an enrolled face can stand in for «Confirmar».
"""

from __future__ import annotations

import asyncio

import structlog

from bridge import face_gate, face_greet, safety
from bridge.routes.common import NO_STORE, json_body, no_store, origin_of
from bridge.routes.safety import confirmable, unproven
from bridge.server import mcp

log = structlog.get_logger(__name__)


# -- faces: who is enrolled, the live settings, enrollment (bridge/face_gate.py) ----------
#
# Seeing a face is like confirming an action, so everything here but /face/seen takes the
# same proof as /safety/confirm: apps/back's SAFETY_CONFIRM_SECRET in
# `X-C3PO-Confirm-Secret`. /face/seen stays open because the bridge listens on loopback
# only (BRIDGE_HOST 127.0.0.1): apps/back relays it to signed-in users.

_enrolling = asyncio.Lock()


@mcp.custom_route("/face/people", methods=["GET"])
async def face_people(request):  # noqa: ANN001, ANN201 - starlette types
    """Everyone enrolled (no features, no photos), the live settings, strangers seen."""
    if (refused := unproven(request)) is not None:
        return refused
    people = await asyncio.to_thread(lambda: [face_gate.public(p) for p in face_gate.people()])
    return no_store(
        {
            "settings": face_gate.settings(),
            "people": people,
            "unknown_seen": face_greet.unknown_seen(),
            "models": face_gate.models_installed(),
        }
    )


@mcp.custom_route("/face/confirm_check", methods=["POST"])
async def face_confirm_check(request):  # noqa: ANN001, ANN201 - starlette types
    """Whether an enrolled person's live face is in view now, to stand in for «Confirmar».

    Body `{"tool": name}`. Grants nothing: apps/back asks this only for a refused call its
    console still shows, and on a pass grants through /safety/confirm (via "face") and runs
    that one call, exactly as a click does. `reach_m` (a T2 under the person rule) is how
    far everyone must be first: the face had to be within MAX_M, inside an arm's reach, so
    back says «alejate» and waits for /safety/status to show nobody that close.
    """
    if (refused := unproven(request)) is not None:
        return refused
    if not safety.FACE_CONFIRM:
        return no_store({"error": "face_confirm_off"}, 403)
    try:
        tool = str((await request.json()).get("tool") or "")
    except Exception:
        tool = ""
    listed = confirmable(tool)
    if listed is None:
        return no_store({"error": "not_confirmable", "tool": tool}, 400)
    # background: a gated call waits this out instead of being refused by it.
    code, detail = await face_gate.live_check(background=True)
    if code != face_gate.PASSED:
        reason = face_gate.NOT_SEEN if code in (face_gate.NO_FACE, face_gate.UNKNOWN) else code
        return no_store({"passed": False, "reason": reason})
    who = await asyncio.to_thread(face_gate.person, detail)
    log.warning("face_gate.confirm_seen", tool=tool, person=detail)
    person = {k: (who or {}).get(k) for k in ("name", "role")} | {"id": detail}
    return no_store(
        {"passed": True, "person": person, "reach_m": safety.reach_m(tool, listed.meta)}
    )


@mcp.custom_route("/face/seen", methods=["GET"])
async def face_seen(request):  # noqa: ANN001, ANN201 - starlette types
    """The faces of the greeter's last check, as boxes in /frame.jpg pixels, for the console
    to draw. Names and roles of enrolled people only; a stranger is a box. Fresh only while
    greet or greet_unknown is on (age_s grows otherwise).

    Readable cross-origin like /camera/*: the boxes are drawn over that picture."""
    from starlette.responses import JSONResponse

    from bridge.sdk.camera_relay import camera_headers

    body = {**face_gate.seen(), "greet": face_greet.last_greet()}
    return JSONResponse(body, headers=camera_headers(origin_of(request)))


@mcp.custom_route("/face/settings", methods=["POST"])
async def face_settings(request):  # noqa: ANN001, ANN201 - starlette types
    """Body: any of {"greet", "greet_unknown", "gate"} as booleans. Applies at once, persists."""
    if (refused := unproven(request)) is not None:
        return refused
    try:
        new = await asyncio.to_thread(face_gate.update_settings, await json_body(request))
    except (ValueError, face_gate.Unavailable) as exc:
        return no_store({"error": "invalid_settings", "reason": str(exc)}, 400)
    log.warning("face.settings_changed", **new)
    if any(new.values()):
        # Off the loop, on the gate's own worker: a cold cv2 import must not hold a stop.
        asyncio.get_running_loop().run_in_executor(face_gate._pool, face_gate.warm)
    return no_store(new)


@mcp.custom_route("/face/people/{pid}", methods=["DELETE"])
async def face_delete(request):  # noqa: ANN001, ANN201 - starlette types
    if (refused := unproven(request)) is not None:
        return refused
    pid = str(request.path_params.get("pid", ""))
    if not await asyncio.to_thread(face_gate.delete_person, pid):
        return no_store({"error": "not_found"}, 404)
    log.warning("face.person_deleted", person=pid)
    return no_store({"deleted": pid})


@mcp.custom_route("/face/people/{pid}", methods=["PATCH"])
async def face_set_role(request):  # noqa: ANN001, ANN201 - starlette types
    """Body {"role": "superadmin" | "operator"}: change who someone is, nothing else."""
    if (refused := unproven(request)) is not None:
        return refused
    pid = str(request.path_params.get("pid", ""))
    role = (await json_body(request)).get("role")
    if role not in face_gate.ROLES:
        bad = {"error": "invalid", "code": "invalid", "reason": f"role must be one of {', '.join(face_gate.ROLES)}"}
        return no_store(bad, 422)
    p = await asyncio.to_thread(face_gate.set_role, pid, role)
    if p is None:
        return no_store({"error": "not_found"}, 404)
    log.warning("face.person_role_changed", person=pid, role=role, via="console")
    return no_store(p)


@mcp.custom_route("/face/people/{pid}/photo.jpg", methods=["GET"])
async def face_photo(request):  # noqa: ANN001, ANN201 - starlette types
    """The enrolled person's own consented photo; never anything of anyone else."""
    from starlette.responses import Response

    if (refused := unproven(request)) is not None:
        return refused
    pid = str(request.path_params.get("pid", ""))
    jpg = None
    if await asyncio.to_thread(face_gate.person, pid) is not None:
        path = face_gate.people_dir() / pid / "photo.jpg"
        jpg = await asyncio.to_thread(face_gate.read_owner_only, path)
    if jpg is None:
        return no_store({"error": "not_found"}, 404)
    return Response(jpg, media_type="image/jpeg", headers=NO_STORE)


@mcp.custom_route("/face/enroll", methods=["POST"])
async def face_enroll(request):  # noqa: ANN001, ANN201 - starlette types
    """Body {"name", "role", "replace"?}: the person stands at the robot, alone, facing the
    head camera.

    The same enrollment as scripts/face_enroll.py (FRAMES live frames, one face, one
    person), in a thread, within face_gate.ENROLL_DEADLINE_S. Their consent is the
    console's to ask before calling this. Every refusal carries a `code`: 400 invalid,
    409 busy or exists (that name is enrolled and `replace` is not true), 422
    no_live_face, not_same_person, camera_unavailable (camera, models or disk).
    """
    if (refused := unproven(request)) is not None:
        return refused
    body = await json_body(request)
    name, role = body.get("name"), body.get("role")
    if not isinstance(name, str) or role not in face_gate.ROLES:
        bad = {"error": "invalid", "code": "invalid", "reason": "name and role are required"}
        return no_store(bad, 400)
    if _enrolling.locked():
        busy = {"error": "busy", "code": "busy", "reason": "an enrollment is running"}
        return no_store(busy, 409)
    async with _enrolling:
        try:
            p = await asyncio.to_thread(
                face_gate.enroll, name, role, face_gate.FRAMES, replace=body.get("replace") is True
            )
        except ValueError as exc:
            return no_store({"error": "invalid", "code": "invalid", "reason": str(exc)}, 400)
        except face_gate.Refused as exc:
            body = {"error": "not_enrolled", "code": exc.code, "reason": str(exc)}
            return no_store(body, 409 if exc.code == "exists" else 422)
        except Exception as exc:  # noqa: BLE001 - camera, models, disk; nothing was stored
            body = {"error": "not_enrolled", "code": "camera_unavailable", "reason": str(exc)}
            return no_store(body, 422)
    log.warning("face.person_enrolled", person=p["id"], role=role, via="console")
    return no_store(p)
