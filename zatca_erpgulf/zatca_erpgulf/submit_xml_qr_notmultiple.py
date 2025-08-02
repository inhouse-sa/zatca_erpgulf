"""This module is used to submit the POS invoice to ZATCA using the API through xml and qr."""

import base64
from frappe import _
import frappe
import requests
from lxml import etree

CONTENT_TYPE_JSON = "application/json"
NOT_SUBMITTED = "Not Submitted"
SALES_INVOICE = "Sales Invoice"


def xml_base64_decode(signed_xmlfile_name):
    """xml base64 decode"""
    try:
        with open(signed_xmlfile_name, "r", encoding="utf-8") as file:
            xml = file.read().lstrip()
            base64_encoded = base64.b64encode(xml.encode("utf-8"))
            base64_decoded = base64_encoded.decode("utf-8")
            return base64_decoded
    except (ValueError, TypeError, KeyError) as e:
        frappe.throw(_(("xml decode base64in simplifed" f"error: {str(e)}")))
        return None


def get_api_url(company_abbr, base_url):
    """There are many api susing in zatca which can be defined by a feild in settings"""
    try:
        company_doc = frappe.get_doc("Company", {"abbr": company_abbr})
        if company_doc.custom_select == "Sandbox":
            url = company_doc.custom_sandbox_url + base_url
        elif company_doc.custom_select == "Simulation":
            url = company_doc.custom_simulation_url + base_url
        else:
            url = company_doc.custom_production_url + base_url

        return url

    except (ValueError, TypeError, KeyError) as e:
        frappe.throw(_(("get api url in simplifed" f"error: {str(e)}")))
        return None


def success_log(response, uuid1, invoice_number):
    """defining the success log"""
    try:
        current_time = frappe.utils.now()
        frappe.get_doc(
            {
                "doctype": "ZATCA ERPGulf Success Log",
                "title": "ZATCA invoice call done successfully",
                "message": "This message by ZATCA Compliance",
                "uuid": uuid1,
                "invoice_number": invoice_number,
                "time": current_time,
                "zatca_response": response,
            }
        ).insert(ignore_permissions=True)
    except (ValueError, TypeError, KeyError, frappe.ValidationError) as e:
        frappe.throw(_(("error in success log in simplifed" f"error: {str(e)}")))
        return None


def error_log():
    """defining the error log"""
    try:
        frappe.log_error(
            title="ZATCA invoice call failed in clearance status",
            message=frappe.get_traceback(),
        )
    except (ValueError, TypeError, KeyError, frappe.ValidationError) as e:
        frappe.throw(_(("error in error login simplifed" f"error: {str(e)}")))
        return None


def extract_uuid_and_invoicehash_simplifeid(file_path):
    """
    Extracts the UUID and DigestValue from an XML file.

    Args:
        file_path (str): Path to the XML file.

    Returns:
        dict: A dictionary containing UUID and DigestValue.
    """
    try:
        # Read the file content as bytes
        with open(frappe.local.site + file_path, "rb") as file:
            custom_xml = file.read()

        # Parse the XML string as bytes
        tree = etree.fromstring(custom_xml)

        # Define the namespaces
        namespaces = {
            "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
            "ds": "http://www.w3.org/2000/09/xmldsig#",
        }

        # Extract the UUID
        uuid = tree.find("cbc:UUID", namespaces).text

        # Extract the DigestValue
        digest_value_element = tree.find(
            ".//ds:Reference[@Id='invoiceSignedData']/ds:DigestValue", namespaces
        )
        digest_value = (
            digest_value_element.text
            if digest_value_element is not None
            else "Not Found"
        )

        return uuid, digest_value

    except Exception as e:
        return {"error": f"Error extracting data in simplifed: {e}"}


def reporting_api_xml_sales_invoice_simplified(
    uuid1, encoded_hash, signed_xmlfile_name, invoice_number, sales_invoice_doc
):
    """Reporting API based on the API data and payload."""
    try:
        company_abbr = frappe.db.get_value(
            "Company", {"name": sales_invoice_doc.company}, "abbr"
        )
        if not company_abbr:
            frappe.throw(
                _(f"Company with abbreviation {sales_invoice_doc.company} not found.")
            )

        company_doc = frappe.get_doc("Company", {"abbr": company_abbr})
        production_csid = get_production_csid(sales_invoice_doc, company_doc)
        headers = get_headers(production_csid)
        payload = {
            "invoiceHash": encoded_hash,
            "uuid": uuid1,
            "invoice": xml_base64_decode(signed_xmlfile_name),
        }

        send_request_and_handle_response(
            company_abbr,
            invoice_number,
            payload,
            headers,
            sales_invoice_doc,
            encoded_hash,
            uuid1,
        )

    except (ValueError, TypeError, KeyError, frappe.ValidationError) as e:
        handle_api_error(invoice_number, e)


def get_production_csid(sales_invoice_doc, company_doc):
    """get production csid"""
    if sales_invoice_doc.custom_zatca_pos_name:
        zatca_settings = frappe.get_doc(
            "ZATCA Multiple Setting", sales_invoice_doc.custom_zatca_pos_name
        )
        return zatca_settings.custom_final_auth_csid
    return company_doc.custom_basic_auth_from_production


def get_headers(production_csid):
    """ "get headers"""
    if not production_csid:
        frappe.throw(_("Production CSID is missing in ZATCA XML."))
    return {
        "accept": CONTENT_TYPE_JSON,
        "accept-language": "en",
        "Clearance-Status": "0",
        "Accept-Version": "V2",
        "Authorization": "Basic " + production_csid,
        "Content-Type": CONTENT_TYPE_JSON,
        "Cookie": "TS0106293e=0132a679c0639d13d069bcba831384623a2ca6da47fac8d91bef610c47c7119dcdd3b817f963ec301682dae864351c67ee3a402866",
    }


def send_request_and_handle_response(
    company_abbr,
    invoice_number,
    payload,
    headers,
    sales_invoice_doc,
    encoded_hash,
    uuid1,
):
    """send_request_and_handle_response"""
    frappe.publish_realtime(
        "show_gif",
        {"gif_url": "/assets/zatca_erpgulf/js/loading.gif"},
        user=frappe.session.user,
    )
    response = requests.post(
        url=get_api_url(company_abbr, base_url="invoices/reporting/single"),
        headers=headers,
        json=payload,
        timeout=300,
    )
    frappe.publish_realtime("hide_gif", user=frappe.session.user)
    if response.status_code in (400, 405, 406, 409):
        handle_failed_submission(
            invoice_number,
            response,
            "Error: The request you are sending to ZATCA is in incorrect format."
            " Please report to system administrator.",
        )
    elif response.status_code in (401, 403, 407, 451):
        handle_failed_submission(
            invoice_number,
            response,
            "Error: ZATCA Authentication failed. "
            "Your access token may be expired or not valid."
            " Please contact your system administrator.",
        )
    elif response.status_code not in (200, 202):
        handle_failed_submission(
            invoice_number,
            response,
            "Error: ZATCA server busy or not responding."
            " Try after sometime or contact your system administrator.",
        )
    else:
        handle_successful_submission(
            invoice_number, response, sales_invoice_doc, encoded_hash, uuid1
        )


def handle_failed_submission(invoice_number, response, error_message):
    """handle_failed_submission"""
    update_invoice_status(invoice_number, NOT_SUBMITTED)
    frappe.throw(
        _(f"{error_message} Status code: {response.status_code}<br><br>{response.text}")
    )


def handle_successful_submission(
    invoice_number, response, sales_invoice_doc, encoded_hash, uuid1
):
    """handle_successful_submission"""
    msg = (
        "SUCCESS: <br><br>"
        if response.status_code == 200
        else "REPORTED WITH WARNINGS: <br><br> Please copy the below message"
        " and send it to your system administrator "
        "to fix this warnings before next submission <br><br>"
    )
    msg += f"Status Code: {response.status_code}<br><br> ZATCA Response: {response.text}<br><br>"

    update_company_or_pos_settings(sales_invoice_doc, encoded_hash, msg)
    update_invoice_status(invoice_number, "REPORTED", uuid1, msg)
    success_log(response.text, uuid1, invoice_number)


def update_invoice_status(invoice_number, status, uuid1=None, msg=None):
    """update_invoice_status"""
    invoice_doc = frappe.get_doc(SALES_INVOICE, invoice_number)
    invoice_doc.db_set(
        "custom_uuid", uuid1 or NOT_SUBMITTED, commit=True, update_modified=True
    )
    invoice_doc.db_set("custom_zatca_status", status, commit=True, update_modified=True)
    invoice_doc.db_set(
        "custom_zatca_full_response",
        msg or NOT_SUBMITTED,
        commit=True,
        update_modified=True,
    )


def update_company_or_pos_settings(sales_invoice_doc, encoded_hash, msg):
    """update_company_or_pos_settings"""
    if sales_invoice_doc.custom_zatca_pos_name:
        zatca_settings = frappe.get_doc(
            "ZATCA Multiple Setting", sales_invoice_doc.custom_zatca_pos_name
        )
        if zatca_settings.custom_send_pos_invoices_to_zatca_on_background:
            frappe.msgprint(msg)
        zatca_settings.custom_pih = encoded_hash
        zatca_settings.save(ignore_permissions=True)
    else:
        company_doc = frappe.get_doc("Company", sales_invoice_doc.company)
        if company_doc.custom_send_einvoice_background:
            frappe.msgprint(msg)
        company_doc.custom_pih = encoded_hash
        company_doc.save(ignore_permissions=True)


def handle_api_error(invoice_number, error):
    """handle_api_error"""
    update_invoice_status(invoice_number, NOT_SUBMITTED, msg=f"Error: {str(error)}")
    frappe.throw(
        _(f"Error in reporting API-1 sales invoice with XML simplified: {str(error)}")
    )


def submit_sales_invoice_simplifeid(sales_invoice_doc, file_path, invoice_number):
    """submit sales invoice with xml qr"""
    try:
        uuid1, encoded_hash = extract_uuid_and_invoicehash_simplifeid(file_path)

        if "error" in uuid1:
            frappe.throw(uuid1["error"])

        reporting_api_xml_sales_invoice_simplified(
            uuid1,
            encoded_hash,
            frappe.local.site + file_path,
            invoice_number,
            sales_invoice_doc,
        )

    except Exception as e:
        frappe.throw(_(f"Error in submitting sales in simplifed: {str(e)}"))
