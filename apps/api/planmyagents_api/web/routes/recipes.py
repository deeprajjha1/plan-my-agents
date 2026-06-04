"""Saved-recipe CRUD routes.

Extracted from ``planmyagents_api.web.app`` on 2026-05-19. These
three endpoints (``POST /recipes``, ``GET /recipes``, ``DELETE
/recipes/{id}``) are the customer-facing surface of the
:mod:`planmyagents_api.marketplace_store` — the persistence layer
behind the "save this recipe to my account" UI on the goal page.

Owner-scoped semantics
----------------------
All three endpoints scope reads/writes to the caller's Clerk user
via the ``current_user`` dependency. We deliberately never reveal
the existence of recipes the caller doesn't own — a 404 on
``DELETE /recipes/{id}`` is returned whether the recipe is missing
*or* owned by someone else. Mixing the two would leak a tenancy
oracle.

Idempotency
-----------
Save is intentionally non-idempotent: saving the same goal_id +
format twice creates two rows. The UI is expected to dedupe on
display when that's what the user wants; the alternative
("automatic dedupe") would silently swallow "save with new notes"
flows. An Update endpoint covering true mutate-existing semantics
lands in Sprint 5 (T3).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from planmyagents_api.auth import CurrentUser, current_user
from planmyagents_api.marketplace_store.store import RecipeNotFoundError
from planmyagents_api.planner.recipe_export import RECIPE_FORMATS
from planmyagents_api.web.models import (
    SavedRecipeResponse,
    SavedRecipesListResponse,
    SaveRecipeRequest,
)

router = APIRouter()


@router.post(
    "/recipes",
    response_model=SavedRecipeResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["recipes"],
)
def save_recipe(
    body: SaveRecipeRequest,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> SavedRecipeResponse:
    """Persist a previously-planned recipe to the caller's account.

    Auth: Clerk-gated. RBAC: scoped to the caller's default
    workspace (multi-workspace support lands in Sprint 5).

    Idempotency: not enforced server-side — saving the same
    goal_id + format twice creates two rows. The UI can implement
    client-side de-dup if desired; we keep server semantics
    explicit so a deliberate "save again with new notes" path
    works without an Update endpoint (covered in T3 / Sprint 5).
    """

    from planmyagents_api.web.app import (
        _goal_cache,
        _marketplace_store,
        _saved_recipe_to_response,
    )

    if body.format not in RECIPE_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "unknown_format",
                "message": f"Unknown recipe format: {body.format!r}",
                "supported_formats": sorted(RECIPE_FORMATS),
            },
        )
    cached = _goal_cache().load(body.goal_id)
    if cached is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "goal_expired",
                "message": (
                    f"goal_id `{body.goal_id}` is no longer cached. "
                    "Re-submit the goal via /goal to refresh, then "
                    "retry saving."
                ),
            },
        )

    store = _marketplace_store()
    workspace = store.default_workspace_for_user(user_id=user.user_id)
    # We persist the cached plan_payload (not a rendered string) so
    # /recipes/{id} can re-render to any format the user picks later
    # — and so future renderer bug-fixes apply retroactively.
    plan_payload = dict(cached.plan_payload)
    plan_payload["goal_id"] = cached.goal_id
    plan_payload["saved_format"] = body.format
    try:
        recipe = store.save_recipe(
            user_id=user.user_id,
            workspace_id=workspace.workspace_id,
            goal=cached.goal_text,
            recipe_json=plan_payload,
            format=body.format,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_recipe", "message": str(exc)},
        ) from exc
    return _saved_recipe_to_response(recipe)


@router.get(
    "/recipes",
    response_model=SavedRecipesListResponse,
    tags=["recipes"],
)
def list_recipes(
    user: Annotated[CurrentUser, Depends(current_user)],
    limit: int = Query(default=50, ge=1, le=200),
) -> SavedRecipesListResponse:
    from planmyagents_api.web.app import (
        _marketplace_store,
        _saved_recipe_to_response,
    )

    store = _marketplace_store()
    recipes = store.list_recipes(user_id=user.user_id)[:limit]
    return SavedRecipesListResponse(
        total=len(recipes),
        recipes=[_saved_recipe_to_response(r) for r in recipes],
    )


@router.delete(
    "/recipes/{recipe_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["recipes"],
)
def delete_recipe(
    recipe_id: str,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> Response:
    from planmyagents_api.web.app import _marketplace_store

    store = _marketplace_store()
    try:
        store.delete_recipe(user_id=user.user_id, recipe_id=recipe_id)
    except RecipeNotFoundError as exc:
        # Owner-scoped lookup — never reveal existence to non-owners.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "recipe_not_found",
                "message": f"No saved recipe with id `{recipe_id}`.",
            },
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
