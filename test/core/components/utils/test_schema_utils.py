"""Tests for core/components/utils/schema_utils.py."""

from __future__ import annotations

from typing import Annotated

from src.core.components.utils.schema_utils import map_type_to_json, parse_function_signature


class TestSchemaUtils:
    """Test cases for schema utils."""

    def test_map_type_to_json_string_annotation_safe(self) -> None:
        """String annotations should not be eval'd and should be safely mapped."""
        assert map_type_to_json("int") == "integer"
        assert map_type_to_json("str") == "string"
        assert map_type_to_json("NotARealType") == "string"

    def test_parse_function_signature_uses_annotated_and_args_doc(self) -> None:
        """Annotated metadata and Args docstring should populate descriptions."""

        def example(
            self,
            x: Annotated[int, "数字X"],
            y: str = "default",
            *args: object,
            **kwargs: object,
        ) -> None:
            """Example function.

            Args:
                x: 来自 docstring 的 x 描述（应被 Annotated 覆盖）
                y: y 的描述

            Returns:
                None
            """

        schema = parse_function_signature(example, "example", "desc")
        props = schema["function"]["parameters"]["properties"]
        required = schema["function"]["parameters"]["required"]

        assert set(props.keys()) == {"x", "y"}
        assert props["x"]["type"] == "integer"
        assert props["x"]["description"] == "数字X"
        assert props["y"]["type"] == "string"
        assert props["y"]["description"] == "y 的描述"
        assert required == ["x"]
