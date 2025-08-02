from setuptools import setup, find_packages

with open("requirements.txt") as f:
    requirements = f.read().splitlines()

setup(
    name="metascan",
    version="0.1.0",
    description="AI-generated media browser with metadata extraction",
    author="Metascan",
    packages=find_packages(),
    install_requires=requirements,
    python_requires=">=3.8",
    entry_points={
        "console_scripts": [
            "metascan=metascan.ui.main_window:main",
        ],
    },
)