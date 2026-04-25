import FinanceDataReader as fdr


def main():
    df_krx = fdr.StockListing("KRX")
    print(df_krx)


if __name__ == "__main__":
    main()
