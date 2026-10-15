import pytest
from sqlalchemy import select

from cwjh.models import Bay, Ingredient, Recipe
from cwjh.protocol import MAX_GLASS_ML
from cwjh.services.cocktail import CocktailError, build_plan


def load(session, name):
    return session.scalar(select(Recipe).where(Recipe.name == name))


def fill(session, bay_id, ingredient, volume=700.0):
    bay = session.get(Bay, bay_id)
    bay.ingredient = session.scalar(select(Ingredient).where(Ingredient.name == ingredient))
    bay.volume_ml = volume


def bays(session):
    return list(session.scalars(select(Bay)))


def test_plan_maps_liquids_to_bays_and_keeps_solids_manual(sessions):
    with sessions() as s:
        fill(s, 3, "Vodka")
        fill(s, 7, "Orange Juice")
        plan = build_plan(load(s, "Screwdriver"), bays(s))
    assert [(st.ingredient, st.manual, st.bay) for st in plan.steps] == [
        ("Ice Cubes", True, None),
        ("Vodka", False, 3),
        ("Orange Juice", False, 7),
    ]
    assert plan.total_ml == 200


def test_missing_ingredient_is_reported(sessions):
    with sessions() as s:
        fill(s, 1, "Vodka")
        with pytest.raises(CocktailError, match="Orange Juice"):
            build_plan(load(s, "Screwdriver"), bays(s))


def test_bay_with_too_little_left_is_not_used(sessions):
    with sessions() as s:
        fill(s, 1, "Vodka", volume=20)
        fill(s, 2, "Orange Juice")
        with pytest.raises(CocktailError, match="Vodka"):
            build_plan(load(s, "Screwdriver"), bays(s))


def test_second_bay_with_same_liquid_is_used(sessions):
    with sessions() as s:
        fill(s, 1, "Vodka", volume=20)
        fill(s, 2, "Vodka", volume=100)
        fill(s, 3, "Orange Juice")
        plan = build_plan(load(s, "Screwdriver"), bays(s))
    assert plan.steps[1].bay == 2


def test_strength_scales_only_alcohol(sessions):
    with sessions() as s:
        fill(s, 1, "Vodka")
        fill(s, 2, "Orange Juice")
        plan = build_plan(load(s, "Screwdriver"), bays(s), strength=1.5)
    assert plan.steps[1].quantity == 75
    assert plan.steps[2].quantity == 150


def test_plan_never_exceeds_glass_limit(sessions):
    with sessions() as s:
        recipe = load(s, "Screwdriver")
        for ri in recipe.ingredients:
            ri.quantity *= 3
        fill(s, 1, "Vodka", volume=2000)
        fill(s, 2, "Orange Juice", volume=2000)
        plan = build_plan(recipe, bays(s), strength=1.5)
        s.rollback()
    assert plan.total_ml <= MAX_GLASS_ML
    vodka, juice = plan.steps[1].quantity, plan.steps[2].quantity
    assert vodka / juice == pytest.approx(0.5, abs=0.01)


def test_strength_out_of_range(sessions):
    with sessions() as s:
        with pytest.raises(CocktailError, match="strength"):
            build_plan(load(s, "Screwdriver"), bays(s), strength=3)
