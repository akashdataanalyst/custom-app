from pathlib import Path

from setuptools import find_packages, setup


def get_requirements():
    requirements_file = Path(__file__).parent / "requirements.txt"

    if not requirements_file.exists():
        return []

    return [
        line.strip()
        for line in requirements_file.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def get_version():
    version_file = Path(__file__).parent / "calco_erp" / "__init__.py"

    for line in version_file.read_text().splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")

    raise RuntimeError("Unable to find __version__ in calco_erp/__init__.py")


setup(
    name="calco_erp",
    version=get_version(),
    description="Calco PolyTechnik Pvt Ltd ERP custom app for ERPNext",
    author="Codex",
    author_email="support@example.com",
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    install_requires=get_requirements(),
)
