"""Bytes-only PyMuPDF adapter; no caller-supplied filename or file object."""


def open_pdf_bytes(data: bytes):
    if type(data) is not bytes:
        raise TypeError('PDF input must be bytes')
    import fitz

    return fitz.open(stream=data, filetype='pdf')
