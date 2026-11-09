// d3studio native core, шаг 1: `d3mesh verify <файл.glb>`.
//
// Зачем нативный бинарник: проверка контейнера и контрольная сумма нужны на каждом ассете,
// а в Node это чтение всего файла в память JS-объектом. Здесь один проход по байтам.
// Сверх этого шага ничего не обещаем: тяжёлые операции (сварка вершин, децимация, генерация
// нормалей, атласы) переезжают сюда по одной, с паритет-тестом против JS-реализации.
//
// Печатает JSON одной строкой в stdout:
//   {"ok":true,"bytes":N,"version":2,"json_chunk":N,"bin_chunk":N,"crc32":"0x1234abcd"}
//   {"ok":false,"error":"текст причины"}
// Коды выхода: 0 — файл целый, 1 — файл битый, 2 — неверный вызов.
use std::env;
use std::fs;
use std::process::ExitCode;

const MAGIC: u32 = 0x4654_6c67; // "glTF" в little-endian
const CHUNK_JSON: u32 = 0x4e4f_534a;
const CHUNK_BIN: u32 = 0x004e_4942;

fn crc32(data: &[u8]) -> u32 {
    let mut crc: u32 = 0xffff_ffff;
    for byte in data {
        crc ^= *byte as u32;
        for _ in 0..8 {
            if crc & 1 == 1 {
                crc = (crc >> 1) ^ 0xedb8_8320;
            } else {
                crc >>= 1;
            }
        }
    }
    !crc
}

fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 8);
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push(' '),
            c => out.push(c),
        }
    }
    out
}

fn fail(message: &str) -> ExitCode {
    println!("{{\"ok\":false,\"error\":\"{}\"}}", escape(message));
    ExitCode::from(1)
}

fn u32_at(data: &[u8], offset: usize) -> u32 {
    let mut bytes = [0u8; 4];
    bytes.copy_from_slice(&data[offset..offset + 4]);
    u32::from_le_bytes(bytes)
}

fn verify(data: &[u8]) -> Result<String, String> {
    if data.len() < 20 {
        return Err("Файл не GLB: меньше 20 байт".to_string());
    }
    if u32_at(data, 0) != MAGIC {
        return Err("Файл не GLB: нет подписи glTF".to_string());
    }
    let version = u32_at(data, 4);
    if version != 2 {
        return Err(format!("GLB версии {}: поддерживается только 2", version));
    }
    let declared = u32_at(data, 8) as usize;
    if declared != data.len() {
        return Err(format!(
            "GLB обрезан: в заголовке {} байт, в файле {}",
            declared,
            data.len()
        ));
    }
    let mut offset = 12usize;
    let mut json_chunk = 0u32;
    let mut bin_chunk = 0u32;
    let mut seen_json = false;
    while offset + 8 <= data.len() {
        let length = u32_at(data, offset) as usize;
        let kind = u32_at(data, offset + 4);
        let start = offset + 8;
        if start + length > data.len() {
            return Err("GLB: чанк выходит за пределы файла".to_string());
        }
        if kind == CHUNK_JSON && !seen_json {
            seen_json = true;
            json_chunk = length as u32;
        } else if kind == CHUNK_BIN && bin_chunk == 0 {
            bin_chunk = length as u32;
        }
        let padding = (4 - (length % 4)) % 4;
        offset = start + length + padding;
    }
    if !seen_json {
        return Err("GLB: нет JSON-чанка".to_string());
    }
    Ok(format!(
        "{{\"ok\":true,\"bytes\":{},\"version\":{},\"json_chunk\":{},\"bin_chunk\":{},\"crc32\":\"0x{:08x}\"}}",
        data.len(),
        version,
        json_chunk,
        bin_chunk,
        crc32(data)
    ))
}

fn main() -> ExitCode {
    let mut args = env::args().skip(1);
    let command = args.next().unwrap_or_default();
    if command != "verify" {
        eprintln!("d3mesh: неизвестная команда «{}». Есть: verify <файл.glb>", command);
        return ExitCode::from(2);
    }
    let path = match args.next() {
        Some(value) => value,
        None => {
            eprintln!("d3mesh: укажите файл: d3mesh verify model.glb");
            return ExitCode::from(2);
        }
    };
    let data = match fs::read(&path) {
        Ok(bytes) => bytes,
        Err(error) => return fail(&format!("файл не читается: {}", error)),
    };
    match verify(&data) {
        Ok(json) => {
            println!("{}", json);
            ExitCode::SUCCESS
        }
        Err(message) => fail(&message),
    }
}
