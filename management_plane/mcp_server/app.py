from fencio_logger import get_logger

from fastmcp import FastMCP
from starlette.responses import JSONResponse

logger = get_logger(__name__, service_name="prism")

mcp = FastMCP(name="guard-mcp-server")


def initialize_tools():
    """
    Explicitly initialize all MCP tools.
    This ensures tools are loaded and registered before the server starts.
    """
    try:
        # Import tools module to trigger @mcp.tool() decorators
        from . import tools as _tools  # noqa: F401
        logger.info("✅ Tools module imported successfully")
    except Exception as exc:
        logger.error("❌ Failed to import tools module: %s", exc, exc_info=True)
        raise


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    return JSONResponse({"status": "healthy", "service": "guard-mcp-server"})
