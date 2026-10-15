"""Cocktail database, following the "cocktail database" wiki page plus the bays."""

import enum

from sqlalchemy import Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class IngredientType(str, enum.Enum):
    ALCOHOLIC = "ALCOHOLIC"
    NON_ALCOHOLIC = "NON_ALCOHOLIC"
    SOLID = "SOLID"


class Ingredient(Base):
    __tablename__ = "ingredient"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    base64_image: Mapped[str | None] = mapped_column(Text, default=None)
    type: Mapped[IngredientType] = mapped_column(Enum(IngredientType))
    # Density, so the scale weight can be turned into ml.
    g_per_ml: Mapped[float] = mapped_column(Float, default=1.0)

    @property
    def is_liquid(self) -> bool:
        return self.type is not IngredientType.SOLID


class Recipe(Base):
    __tablename__ = "recipe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    base64_image: Mapped[str | None] = mapped_column(Text, default=None)
    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="recipe", order_by="RecipeIngredient.position", cascade="all, delete-orphan"
    )

    @property
    def alcoholic(self) -> bool:
        return any(ri.ingredient.type is IngredientType.ALCOHOLIC for ri in self.ingredients)


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredient"

    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipe.id"), primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"), primary_key=True)
    # ml for liquids, pieces for solids.
    quantity: Mapped[float] = mapped_column(Float)
    position: Mapped[int] = mapped_column(Integer, default=0)

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")
    ingredient: Mapped[Ingredient] = relationship()


class Bay(Base):
    __tablename__ = "bay"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredient.id"), default=None)
    volume_ml: Mapped[float] = mapped_column(Float, default=0.0)

    ingredient: Mapped[Ingredient | None] = relationship()

    @property
    def filled(self) -> bool:
        return self.ingredient_id is not None
