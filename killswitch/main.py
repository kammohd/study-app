import base64
import json
import os

import functions_framework
from google.cloud import billing_v1

PROJECT_ID = os.environ["GCP_PROJECT"]


@functions_framework.cloud_event
def stop_billing(cloud_event):
    """Budget alert -> unlink billing from the project once spend reaches the budget."""
    data = json.loads(base64.b64decode(cloud_event.data["message"]["data"]))
    cost, budget = data["costAmount"], data["budgetAmount"]
    if cost < budget:
        print(f"{cost} of {budget}: under budget, nothing to do")
        return

    client = billing_v1.CloudBillingClient()
    name = f"projects/{PROJECT_ID}"
    if not client.get_project_billing_info(name=name).billing_enabled:
        print("billing already disabled")
        return
    client.update_project_billing_info(
        name=name, project_billing_info=billing_v1.ProjectBillingInfo(billing_account_name="")
    )
    print(f"{cost} >= {budget}: billing disabled for {PROJECT_ID}")
