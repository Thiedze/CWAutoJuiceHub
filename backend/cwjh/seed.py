"""Sample ingredients and recipes for a fresh database."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Ingredient, IngredientType, Recipe, RecipeIngredient

A, N, S = IngredientType.ALCOHOLIC, IngredientType.NON_ALCOHOLIC, IngredientType.SOLID

INGREDIENTS = [
    ("Vodka", A, 0.95),
    ("White Rum", A, 0.95),
    ("Gin", A, 0.95),
    ("Tequila", A, 0.95),
    ("Orange Juice", N, 1.04),
    ("Pineapple Juice", N, 1.05),
    ("Cranberry Juice", N, 1.04),
    ("Lime Juice", N, 1.03),
    ("Sugar Syrup", N, 1.30),
    ("Tonic Water", N, 1.02),
    ("Soda Water", N, 1.00),
    ("Grenadine", N, 1.30),
    ("Ice Cubes", S, 1.0),
    ("Lime Wedge", S, 1.0),
    ("Mint Leaves", S, 1.0),
]

# name -> [(ingredient, quantity)] in pouring order
RECIPES = {
    "Screwdriver": [("Ice Cubes", 4), ("Vodka", 50), ("Orange Juice", 150)],
    "Gin Tonic": [("Ice Cubes", 4), ("Gin", 50), ("Tonic Water", 150), ("Lime Wedge", 1)],
    "Cape Codder": [("Ice Cubes", 4), ("Vodka", 50), ("Cranberry Juice", 120), ("Lime Wedge", 1)],
    "Tequila Sunrise": [("Ice Cubes", 4), ("Tequila", 50), ("Orange Juice", 120), ("Grenadine", 15)],
    "Mojito": [
        ("Mint Leaves", 8),
        ("Lime Juice", 25),
        ("Sugar Syrup", 20),
        ("White Rum", 50),
        ("Ice Cubes", 4),
        ("Soda Water", 80),
    ],
    "Virgin Sunrise": [("Ice Cubes", 4), ("Orange Juice", 150), ("Grenadine", 15)],
    "Tropical Punch": [("Ice Cubes", 4), ("Pineapple Juice", 100), ("Orange Juice", 80), ("Grenadine", 10)],
    "Cranberry Fizz": [("Ice Cubes", 4), ("Cranberry Juice", 120), ("Lime Juice", 15), ("Soda Water", 80)],
}


def seed(session: Session) -> None:
    if session.scalar(select(Ingredient.id).limit(1)) is not None:
        return
    by_name = {}
    for name, kind, density in INGREDIENTS:
        by_name[name] = Ingredient(name=name, type=kind, g_per_ml=density)
    session.add_all(by_name.values())
    for recipe_name, items in RECIPES.items():
        recipe = Recipe(name=recipe_name)
        recipe.ingredients = [
            RecipeIngredient(ingredient=by_name[name], quantity=qty, position=pos)
            for pos, (name, qty) in enumerate(items)
        ]
        session.add(recipe)
