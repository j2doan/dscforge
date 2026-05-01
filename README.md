# Welcome to dscforge!

A tool to generate data science project templates.

## Install

`pip install dscforge`

## Usage

`dscforge my_project`

### Flags

- `--template template_type` : generates using the specified template
- `--git` : initializes git when generating the template

## Example Usages

- `dscforge my_project` : Creates a default basic project template called "my_project".
- `dscforge my_project --template basic` : Creates a basic project template called "my_project".
- `dscforge my_project --template standard` : Creates a standard project template called "my_project".
- `dscforge my_project --template premium` : Creates a premium project template called "my_project".
- `dscforge my_project --git` : Creates a default basic project template called "my_project" with git initialization.
- `dscforge my_project --template basic --git` : Creates a basic project template called "my_project" with git initialization.
- `dscforge my_project --template standard --git` : Creates a standard project template called "my_project" with git initialization.
- `dscforge my_project --template premium --git` : Creates a premium project template called "my_project" with git initialization.

# Project Templates

## Basic Template

A minimal setup for quick experiments, scripts, or small assignments.

```text id="basic_md"
my_project/
├── main.py
├── main.ipynb
```

### What it’s for

* Quick prototyping
* Class assignments
* Scratch work / experiments
* No setup overhead

---

## Standard Template

A structured setup for data science coursework and small projects.

```text id="standard_md"
my_project/
├── data/
│   ├── raw/
│   └── processed/
│
├── figures/
│
├── notebooks/
│   └── main.ipynb
│
├── src/
│   └── main.py
│
├── .gitignore
├── pyproject.toml
├── README.md
└── requirements.txt
```

### What it’s for

* Data analysis projects
* Coursework / assignments
* Small research projects
* Clean separation of code and data

### Each standard template includes its own isolated virtual environment (`.venv`)
- Dependencies: numpy, pandas, matplotlib, ipykernel

---

## Premium Template

A production-style data science project structure with scalability in mind.

```text id="premium_md"
my_project/
├── data/
│   ├── raw/
│   └── processed/
│
├── figures/
│
├── logs/
│   └── app.log
│
├── models/
│
├── notebooks/
│   └── main.ipynb
│
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── main.py
│   └── utils.py
│
├── tests/
│   └── test.py
│
├── web/
│   ├── pages/
│   │   └── index.html
│   ├── styles/
│   │   └── style.css
│   └── scripts/
│       └── main.js
│
├── .gitignore
├── pyproject.toml
├── README.md
└── requirements.txt
```

### What it’s for

* End-to-end data science projects
* Research pipelines
* ML experimentation
* Dashboards or web-integrated projects
* Scalable production-like structure

### Each premium template includes its own isolated virtual environment (`.venv`)
- Dependencies: numpy, pandas, matplotlib, ipykernel

---

