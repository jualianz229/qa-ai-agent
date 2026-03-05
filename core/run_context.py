def merge_recrawl_project_info(project_info: dict, refreshed_project_info: dict) -> dict:
    for key in ("title", "domain", "project_name"):
        if refreshed_project_info.get(key):
            project_info[key] = refreshed_project_info[key]
    return project_info
