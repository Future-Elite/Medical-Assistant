package com.his.common.common;

import lombok.Data;

@Data
public class Result<T> {
    private int code;
    private String message;
    private T data;

    public static <T> Result<T> ok(T data) {
        Result<T> r = new Result<>();
        r.code = 0; r.message = "success"; r.data = data; return r;
    }
    public static <T> Result<T> fail(String message) {
        Result<T> r = new Result<>();
        r.code = 1; r.message = message; return r;
    }
    /** 带业务错误码的失败返回；前端 axios 拦截器按 `code != 0` 判定为失败。 */
    public static <T> Result<T> fail(int code, String message) {
        Result<T> r = new Result<>();
        r.code = code; r.message = message; return r;
    }
}
