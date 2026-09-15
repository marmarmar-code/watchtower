"""Small generic PDF writer used only by decoder tests."""
def pdf_pages(pages):
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>']
    kids = []
    for cells in pages:
        commands = []
        for x, y, text in cells:
            text = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            commands.append(f'BT /F1 10 Tf 1 0 0 1 {x} {832-y} Tm ({text}) Tj ET')
        stream = ('\n'.join(commands)+'\n').encode('cp1252')
        objects.append(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'endstream')
        ref = len(objects)
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {ref} 0 R >>'.encode()); kids.append(len(objects))
    objects[1] = ('<< /Type /Pages /Count '+str(len(kids))+' /Kids ['+' '.join(f'{k} 0 R' for k in kids)+'] >>').encode()
    raw = bytearray(b'%PDF-1.4\n'); offsets = []
    for i,obj in enumerate(objects,1):
        offsets.append(len(raw)); raw.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref = len(raw); raw.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets: raw.extend(f'{offset:010d} 00000 n \n'.encode())
    raw.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(raw)
