from pathlib import Path

from setuptools import find_packages, setup

ROOT = Path(__file__).parent


def read_requirements():
    with (ROOT / "requirements.txt").open(encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


setup(
    name="NanUI",
    version="0.4.0",
    packages=find_packages(),
    install_requires=read_requirements(),
    extras_require={"webengine": ["PySide6-Addons>=6.5"]},
    description="一个基于 PySide6 的自定义 UI 组件库",
    long_description=(ROOT / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    url="https://github.com/NanbeiTnT/NanUI",
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: User Interfaces",
    ],
    author="NanbeiTnT",
    license="MIT",
    # sdist 由 MANIFEST.in 控制（含 images/*.png 供 qrc 重新编译），
    # wheel 只带下面显式声明的运行资源，避免 setuptools 把 images/ 误当包。
    include_package_data=False,
    package_data={
        "NanUI": [
            "styles/*.qss",
            "resources/*.py",
            "py.typed",
        ],
    },
)
