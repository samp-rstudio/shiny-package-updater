import os
import re

from shiny import App, ui, render, reactive
import base64
import gzip
import json
import pkg_resources
import requests

app_ui = ui.page_fluid(
    ui.navset_tab(
        ui.nav_panel(
            "Update shinytidy manifest",
            ui.layout_sidebar(
                ui.sidebar(
                    ui.input_text("package", "Package name"),
                    ui.input_action_button("craninfo", "Show cran package info"),
                    ui.tags.hr(),
                    ui.input_action_button("generate", "Update shinytidy manifest.json"),
                    ui.input_checkbox("update_in_github", "Save manifest to samp-rstudio/shinytidy"),
                    ui.tags.hr(),
                    ui.input_action_button("show_python", "Show python packages"),
                ),
                ui.navset_card_tab(
                    ui.nav_panel(
                        "Package info from cran",
                        ui.tags.pre(ui.output_text("cran_package_info")),
                        value="package_info"
                    ),
                    ui.nav_panel(
                        "Generated manifest.json",
                        ui.output_text("manifest_desc"),
                        ui.tags.pre(ui.output_text("manifest_output")),
                        value="manifest"
                    ),
                    ui.nav_panel(
                        "Local python packages",
                        ui.tags.pre(ui.output_text("python_pkgs")),
                        value="python"
                    ),
                    id="output_tabs"
                )
            )
        )
    )
)

def server(input, output, session):
    content = read_packages("https://cloud.r-project.org/src/contrib/PACKAGES.gz")
    button_clicks = {"pkg_info": 0, "manifeset": 0, "python": 0}

    @output
    @render.text
    @reactive.event(input.craninfo)
    def cran_package_info():
        package = input.package()
        lines = get_package_lines(content, package)
        return "\n".join(lines)

    @render.text
    def manifest_desc():
        return "Save to GitHub then publish this repo to see results: https://github.com/samp-rstudio/shinytidy"

    @output
    @render.text
    @reactive.event(input.generate)
    def manifest_output():
        # Base packages that are always included
        p = get_packages(content, ["tidyverse", "shiny", "bslib", "DT", "duckdb"])
        packages = {e["key"]: e["value"] for e in p}

        # Create manifest dictionary
        manifest = {
            "version": 1,
            "locale": "C",
            "platform": "4.4.1",
            "metadata": {
                "appmode": "shiny",
                "primary_rmd": None,
                "primary_html": None,
                "content_category": None,
                "has_parameters": False,
            },
            "packages": packages,
            "files": {
                "app.R": {
                    "checksum": "0000000000000000000000000000000"
                }
            },
            "users": None,
        }
        
        # Convert to pretty JSON string
        manifest =  json.dumps(manifest, indent=2)
        if input.update_in_github():
            update_manifest(manifest)
        return manifest

    @output
    @render.text
    @reactive.event(input.show_python)
    def python_pkgs():
        packages = [f"{d.project_name}=={d.version}" for d in pkg_resources.working_set]
        return "\n".join(packages)

    @reactive.effect
    def _():
        show_python = input.show_python()
        show_manifest = input.generate()
        show_pkg_info = input.craninfo()
        if show_python > button_clicks["python"]:
            button_clicks["python"] = button_clicks["python"] + 1
            ui.update_navs("output_tabs", selected="python")
        elif show_manifest > button_clicks["manifeset"]:
            button_clicks["manifeset"] = button_clicks["manifeset"] + 1
            ui.update_navs("output_tabs", selected="manifest")
        elif show_pkg_info > button_clicks["pkg_info"]:
            button_clicks["pkg_info"] = button_clicks["pkg_info"] + 1
            ui.update_navs("output_tabs", selected="package_info")

def read_packages(url: str) -> str:
    # Step 1: Download the file
    response = requests.get(url)
    response.raise_for_status()

    # Step 2: Unzip the content
    return gzip.decompress(response.content).decode('utf-8')

def get_package_lines(content: str, pkg: str) -> list[str]:
    lines = content.splitlines()
    in_block = False

    results = []
    for line in lines:
        if line.startswith(f"Package: {pkg}"):
            in_block = True
        if in_block:
            results.append(line)
            if not line.strip():  # Stop at a blank line
                break
    return results

def get_packages(content: str, pkgs: list[str]) -> list[dict[str, dict]]:
    so_far = [ # base R packages
        "R",
        "base",
        "compiler",
        "datasets",
        "graphics",
        "grDevices",
        "grid",
        "methods",
        "parallel",
        "splines",
        "stats",
        "stats4",
        "tcltk",
        "utils",
        "tools",
    ]
    if len(pkgs) == 0:
        return []
    more_deps = []
    if len(pkgs) > 1:
        more_deps = pkgs[1:]
    return get_package(content, pkgs[0], more_deps, so_far)

def get_package(content: str, pkg: str, more_deps: list[str], so_far: list[str]) -> list[dict[str, dict]]:
    desc = {
        "Package": pkg,
        "Type": "package",

    }
    lines = get_package_lines(content, pkg)
    last_key = None
    last_value = None
    for line in lines:
        if line.startswith(" "):
            last_value = f"{last_value}\n{line.strip()}"
        else:
            if last_key:
                desc[last_key] = last_value
            last_key = None
            last_value = None
        if line.startswith("Version: "):
            desc["Version"] = line.split(": ", 1)[1]
        if line.startswith("License: "):
            desc["License"] = line.split(": ", 1)[1]
        if line.startswith("Depends: "):
            last_key = "Depends"
            last_value = line.split(": ", 1)[1]
        if line.startswith("Imports: "):
            last_key = "Imports"
            last_value = line.split(": ", 1)[1]
        if line.startswith("LinkingTo: "):
            last_key = "LinkingTo"
            last_value = line.split(": ", 1)[1]
        if line.startswith("Suggests: "):
            last_key = "Suggests"
            last_value = line.split(": ", 1)[1]
        if line.startswith("NeedsCompilation: "):
            desc["NeedsCompilation"] = line.split(": ", 1)[1]

    packages = [{
        "key": pkg,
        "value": {
            "Source": "CRAN",
            "Repository": "http://rspm/default/latest",
            "description": desc,
        }
    }]
    debug = "disabled"
    deps = get_dependencies(desc, pkg==debug) + more_deps
    if pkg == debug:
        print(f"{debug} DEPS: {deps}")
    for dep in deps:
        if dep not in so_far:
            so_far.append(dep)
            packages += get_package(content, dep, [], so_far)
    return packages

def get_dependencies(desc: dict[str, str], debug: bool) -> list[str]:
    deps = []
    if "Depends" in desc:
        v = desc["Depends"].replace("\n", " ")
        deps += v.split(",")
    if "Imports" in desc:
        v = desc["Imports"].replace("\n", " ")
        deps += v.split(",")
    if "LinkingTo" in desc:
        v = desc["LinkingTo"].replace("\n", " ")
        deps += v.split(",")

    names = []
    for t in deps:
        if debug:
            print(f"DEP: {t}")
        n = get_package_name(t)
        names += [n]
    return names

def get_package_name(v: str) -> str:
    # regex pull name from pattern: name (version)
    pattern = r"\s*([^ \(]+).*\s*"
    m = re.match(pattern, v)
    if m:
        return m.group(1)
    return ""

def update_manifest(new_content: str):
    url = f"https://api.github.com/repos/samp-rstudio/shinytidy/contents/manifest.json"
    headers = {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json"
    }
    # Get the current file info (to retrieve the SHA)
    response = requests.get(url, headers=headers, params={"ref": "main"})
    if response.status_code == 200:
        file_info = response.json()
        sha = file_info['sha']
    elif response.status_code == 404:
        print("File not found. Ensure the file path is correct.")
        return
    else:
        print(f"Failed to fetch file info: {response.status_code} - {response.text}")
        return

    # Prepare the data for updating the file
    encoded_content = base64.b64encode(new_content.encode('utf-8')).decode('utf-8')
    payload = {
        "message": "New manifest.json",
        "content": encoded_content,
        "sha": sha,
        "branch": "main"
    }

    # Send the PUT request to update the file
    response = requests.put(url, headers=headers, data=json.dumps(payload))
    if response.status_code == 200:
        print("File updated successfully!")
    else:
        print(f"Failed to update the file: {response.status_code} - {response.text}")

app = App(app_ui, server)
