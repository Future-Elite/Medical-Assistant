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
}
