import os


_CACHE_NAMESPACE_ENV = "MW2_RENDERER_NUMBA_CACHE_NAMESPACE"


def configure_numba_cache(package_name):
    """Select a cache isolated from incompatible Python import namespaces."""
    namespace = package_name or "top_level"
    active_namespace = os.environ.get(_CACHE_NAMESPACE_ENV)
    if active_namespace is not None and active_namespace != namespace:
        raise RuntimeError(
            "renderer Numba modules cannot be imported under multiple "
            f"namespaces in one process: {active_namespace!r} and "
            f"{namespace!r}"
        )
    os.environ[_CACHE_NAMESPACE_ENV] = namespace

    configured = os.environ.get("NUMBA_CACHE_DIR")
    if configured:
        return configured

    cache_namespace = namespace.replace(".", "_")
    cache_dir = os.path.join(
        os.path.dirname(__file__),
        "__pycache__",
        f"numba_{cache_namespace}",
    )
    os.environ["NUMBA_CACHE_DIR"] = cache_dir
    return cache_dir
