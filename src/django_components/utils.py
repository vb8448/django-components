import functools
import glob
import re
import sys
import typing
from pathlib import Path
from typing import Any, Callable, List, Mapping, Sequence, Tuple, Type, TypeVar, Union, cast, get_type_hints

from django.template.defaultfilters import escape
from django.utils.autoreload import autoreload_started

from django_components.util.nanoid import generate


# Based on nanoid implementation from
# https://github.com/puyuan/py-nanoid/tree/99e5b478c450f42d713b6111175886dccf16f156/nanoid
def gen_id() -> str:
    """Generate a unique ID that can be associated with a Node"""
    # Alphabet is only alphanumeric. Compared to the default alphabet used by nanoid,
    # we've omitted `-` and `_`.
    # With this alphabet, at 6 chars, the chance of collision is 1 in 3.3M.
    # See https://zelark.github.io/nano-id-cc/
    return generate(
        "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
        size=6,
    )


def find_last_index(lst: List, predicate: Callable[[Any], bool]) -> Any:
    for r_idx, elem in enumerate(reversed(lst)):
        if predicate(elem):
            return len(lst) - 1 - r_idx
    return -1


def is_str_wrapped_in_quotes(s: str) -> bool:
    return s.startswith(('"', "'")) and s[0] == s[-1] and len(s) >= 2


# See https://github.com/EmilStenstrom/django-components/issues/586#issue-2472678136
def watch_files_for_autoreload(watch_list: Sequence[Union[str, Path]]) -> None:
    def autoreload_hook(sender: Any, *args: Any, **kwargs: Any) -> None:
        watch = sender.extra_files.add
        for file in watch_list:
            watch(Path(file))

    autoreload_started.connect(autoreload_hook)


# Get all types that users may use from the `typing` module.
#
# These are the types that we do NOT try to resolve when it's a typed generic,
# e.g. `Union[int, str]`.
# If we get a typed generic that's NOT part of this set, we assume it's a user-made
# generic, e.g. `Component[Args, Kwargs]`. In such case we assert that a given value
# is an instance of the base class, e.g. `Component`.
_typing_exports = frozenset(
    [
        value
        for value in typing.__dict__.values()
        if isinstance(
            value,
            (
                typing._SpecialForm,
                # Used in 3.8 and 3.9
                getattr(typing, "_GenericAlias", ()),
                # Used in 3.11+ (possibly 3.10?)
                getattr(typing, "_SpecialGenericAlias", ()),
            ),
        )
    ]
)


def _prepare_type_for_validation(the_type: Any) -> Any:
    # If we got a typed generic (AKA "subscripted" generic), e.g.
    # `Component[CompArgs, CompKwargs, ...]`
    # then we cannot use that generic in `isintance()`, because we get this error:
    # `TypeError("Subscripted generics cannot be used with class and instance checks")`
    #
    # Instead, we resolve the generic to its original class, e.g. `Component`,
    # which can then be used in instance assertion.
    if hasattr(the_type, "__origin__"):
        is_custom_typing = the_type.__origin__ not in _typing_exports
        if is_custom_typing:
            return the_type.__origin__
        else:
            return the_type
    else:
        return the_type


# NOTE: tuple_type is a _GenericAlias - See https://stackoverflow.com/questions/74412803
def validate_typed_tuple(
    value: Tuple[Any, ...],
    tuple_type: Any,
    prefix: str,
    kind: str,
) -> None:
    # `Any` type is the signal that we should skip validation
    if tuple_type == Any:
        return

    # We do two kinds of validation with the given Tuple type:
    # 1. We check whether there are any extra / missing positional args
    # 2. We look at the members of the Tuple (which are types themselves),
    #    and check if our concrete list / tuple has correct types under correct indices.
    expected_pos_args = len(tuple_type.__args__)
    actual_pos_args = len(value)
    if expected_pos_args > actual_pos_args:
        # Generate errors like below (listed for searchability)
        # `Component 'name' expected 3 positional arguments, got 2`
        raise TypeError(f"{prefix} expected {expected_pos_args} {kind}s, got {actual_pos_args}")

    for index, arg_type in enumerate(tuple_type.__args__):
        arg = value[index]
        arg_type = _prepare_type_for_validation(arg_type)
        if sys.version_info >= (3, 11) and not isinstance(arg, arg_type):
            # Generate errors like below (listed for searchability)
            # `Component 'name' expected positional argument at index 0 to be <class 'int'>, got 123.5 of type <class 'float'>`  # noqa: E501
            raise TypeError(
                f"{prefix} expected {kind} at index {index} to be {arg_type}, got {arg} of type {type(arg)}"
            )


# NOTE:
# - `dict_type` can be a `TypedDict` or `Any` as the types themselves
# - `value` is expected to be TypedDict, the base `TypedDict` type cannot be used
#   in function signature (only its subclasses can), so we specify the type as Mapping.
#   See https://stackoverflow.com/questions/74412803
def validate_typed_dict(value: Mapping[str, Any], dict_type: Any, prefix: str, kind: str) -> None:
    # `Any` type is the signal that we should skip validation
    if dict_type == Any:
        return

    # See https://stackoverflow.com/a/76527675
    # And https://stackoverflow.com/a/71231688
    required_kwargs = dict_type.__required_keys__
    unseen_keys = set(value.keys())

    # For each entry in the TypedDict, we do two kinds of validation:
    # 1. We check whether there are any extra / missing keys
    # 2. We look at the values of TypedDict entries (which are types themselves),
    #    and check if our concrete dict has correct types under correct keys.
    for key, kwarg_type in get_type_hints(dict_type).items():
        if key not in value:
            if key in required_kwargs:
                # Generate errors like below (listed for searchability)
                # `Component 'name' is missing a required keyword argument 'key'`
                # `Component 'name' is missing a required slot argument 'key'`
                # `Component 'name' is missing a required data argument 'key'`
                raise TypeError(f"{prefix} is missing a required {kind} '{key}'")
        else:
            unseen_keys.remove(key)
            kwarg = value[key]
            kwarg_type = _prepare_type_for_validation(kwarg_type)

            # NOTE: `isinstance()` cannot be used with the version of TypedDict prior to 3.11.
            # So we do type validation for TypedDicts only in 3.11 and later.
            if sys.version_info >= (3, 11) and not isinstance(kwarg, kwarg_type):
                # Generate errors like below (listed for searchability)
                # `Component 'name' expected keyword argument 'key' to be <class 'int'>, got 123.4 of type <class 'float'>`  # noqa: E501
                # `Component 'name' expected slot 'key' to be <class 'int'>, got 123.4 of type <class 'float'>`
                # `Component 'name' expected data 'key' to be <class 'int'>, got 123.4 of type <class 'float'>`
                raise TypeError(
                    f"{prefix} expected {kind} '{key}' to be {kwarg_type}, got {kwarg} of type {type(kwarg)}"
                )

    if unseen_keys:
        formatted_keys = ", ".join([f"'{key}'" for key in unseen_keys])
        # Generate errors like below (listed for searchability)
        # `Component 'name' got unexpected keyword argument keys 'invalid_key'`
        # `Component 'name' got unexpected slot keys 'invalid_key'`
        # `Component 'name' got unexpected data keys 'invalid_key'`
        raise TypeError(f"{prefix} got unexpected {kind} keys {formatted_keys}")


TFunc = TypeVar("TFunc", bound=Callable)


def lazy_cache(
    make_cache: Callable[[], Callable[[Callable], Callable]],
) -> Callable[[TFunc], TFunc]:
    """
    Decorator that caches the given function similarly to `functools.lru_cache`.
    But the cache is instantiated only at first invocation.

    `cache` argument is a function that generates the cache function,
    e.g. `functools.lru_cache()`.
    """
    _cached_fn = None

    def decorator(fn: TFunc) -> TFunc:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Lazily initialize the cache
            nonlocal _cached_fn
            if not _cached_fn:
                # E.g. `lambda: functools.lru_cache(maxsize=app_settings.TEMPLATE_CACHE_SIZE)`
                cache = make_cache()
                _cached_fn = cache(fn)

            return _cached_fn(*args, **kwargs)

        # Allow to access the LRU cache methods
        # See https://stackoverflow.com/a/37654201/9788634
        wrapper.cache_info = lambda: _cached_fn.cache_info()  # type: ignore
        wrapper.cache_clear = lambda: _cached_fn.cache_clear()  # type: ignore

        # And allow to remove the cache instance (mostly for tests)
        def cache_remove() -> None:
            nonlocal _cached_fn
            _cached_fn = None

        wrapper.cache_remove = cache_remove  # type: ignore

        return cast(TFunc, wrapper)

    return decorator


def any_regex_match(string: str, patterns: List[re.Pattern]) -> bool:
    return any(p.search(string) is not None for p in patterns)


def no_regex_match(string: str, patterns: List[re.Pattern]) -> bool:
    return all(p.search(string) is None for p in patterns)


def search_dirs(dirs: List[Path], search_glob: str) -> List[Path]:
    """
    Search the directories for the given glob pattern. Glob search results are returned
    as a flattened list.
    """
    matched_files: List[Path] = []
    for directory in dirs:
        for path in glob.iglob(str(Path(directory) / search_glob), recursive=True):
            matched_files.append(Path(path))

    return matched_files


# See https://stackoverflow.com/a/2020083/9788634
def get_import_path(cls_or_fn: Type[Any]) -> str:
    """
    Get the full import path for a class or a function, e.g. `"path.to.MyClass"`
    """
    module = cls_or_fn.__module__
    if module == "builtins":
        return cls_or_fn.__qualname__  # avoid outputs like 'builtins.str'
    return module + "." + cls_or_fn.__qualname__


# See https://stackoverflow.com/a/58800331/9788634
# str.replace(/\\|`|\$/g, '\\$&');
JS_STRING_LITERAL_SPECIAL_CHARS_REGEX = re.compile(r"\\|`|\$")


# See https://stackoverflow.com/a/34064434/9788634
def escape_js_string_literal(js: str) -> str:
    escaped_js = escape(js)

    def on_replace_match(match: "re.Match[str]") -> str:
        return f"\\{match[0]}"

    escaped_js = JS_STRING_LITERAL_SPECIAL_CHARS_REGEX.sub(on_replace_match, escaped_js)
    return escaped_js
